"""Model router — config-driven routing for all LLM calls.

Loads routing configuration from donna_models.yaml and task_types.yaml,
resolves task_type → model alias → provider, and dispatches completions
through the resilience layer. See docs/model-layer.md.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

# Imported lazily to avoid circular dependency: budget → tracker → aiosqlite,
# while router is used by dedup which is used by budget.
# Type-only import is sufficient here.
from typing import TYPE_CHECKING, Any

import structlog

from donna.config import ModelsConfig, TaskTypesConfig
from donna.models.providers import ModelProvider
from donna.models.providers.anthropic import AnthropicProvider
from donna.models.tokens import estimate_tokens
from donna.models.types import CompletionMetadata
from donna.resilience.retry import CircuitBreaker, TaskCategory, resilient_call

if TYPE_CHECKING:
    from donna.cost.budget import BudgetGuard

logger = structlog.get_logger()


class RoutingError(Exception):
    """Raised when a task type or model alias cannot be resolved."""


class ContextOverflowError(Exception):
    """Raised when a prompt exceeds the local-model context budget and no
    fallback is configured. Loud-fail by design: a silently truncated
    prompt produces silent garbage, which is worse."""


# Registry of known provider names → constructor callables.
# OllamaProvider is registered lazily to avoid import errors when
# aiohttp is not available (e.g. lightweight test environments).
_PROVIDER_REGISTRY: dict[str, type] = {
    "anthropic": AnthropicProvider,
}

try:
    from donna.models.providers.ollama import OllamaProvider
    _PROVIDER_REGISTRY["ollama"] = OllamaProvider
except ImportError:
    pass


class ModelRouter:
    """Config-driven model router.

    Routes LLM calls based on task type → model alias → provider,
    wrapping each call with the resilience layer.
    """

    def __init__(
        self,
        models_config: ModelsConfig,
        task_types_config: TaskTypesConfig,
        project_root: Path,
        budget_guard: BudgetGuard | None = None,
        on_shadow_complete: Callable[
            [str, dict[str, Any], CompletionMetadata], Awaitable[None]
        ]
        | None = None,
    ) -> None:
        self._models_config = models_config
        self._task_types_config = task_types_config
        self._project_root = project_root
        self._budget_guard = budget_guard
        self._on_shadow_complete = on_shadow_complete
        self._circuit_breaker = CircuitBreaker()

        # Instantiate one provider instance per unique provider name in config.
        self._providers: dict[str, ModelProvider] = {}
        seen_providers: set[str] = set()
        for alias, mc in models_config.models.items():
            if mc.provider not in seen_providers:
                seen_providers.add(mc.provider)
                cls = _PROVIDER_REGISTRY.get(mc.provider)
                if cls is None:
                    raise RoutingError(
                        f"Unknown provider {mc.provider!r} "
                        f"(referenced by model alias {alias!r})"
                    )
                if mc.provider == "ollama":
                    self._providers[mc.provider] = cls(
                        base_url=models_config.ollama.base_url,
                        timeout_s=models_config.ollama.timeout_s,
                        estimated_cost_per_1k_tokens=(
                            mc.estimated_cost_per_1k_tokens or 0.0001
                        ),
                    )
                elif mc.provider == "anthropic":
                    self._providers[mc.provider] = cls()
                else:
                    self._providers[mc.provider] = cls()

        # Cache for loaded prompt templates and schemas.
        self._prompt_cache: dict[str, str] = {}
        self._schema_cache: dict[str, dict[str, Any]] = {}

    def _resolve_route(self, task_type: str) -> tuple[ModelProvider, str, str]:
        """Resolve task_type → (provider instance, model ID, model alias).

        Exact-key match takes precedence. When no exact key matches, fall back
        to longest-prefix match on the "::"-separated task_type. Lets callers
        pass dynamic task_types like ``skill_step::<cap>::<step>`` without
        registering every combination in donna_models.yaml.

        Raises RoutingError if neither exact nor any prefix match.
        """
        routing = self._models_config.routing.get(task_type)
        if routing is None:
            # Prefix fallback — try progressively shorter prefixes on "::".
            parts = task_type.split("::")
            for i in range(len(parts) - 1, 0, -1):
                candidate = "::".join(parts[:i])
                routing = self._models_config.routing.get(candidate)
                if routing is not None:
                    break
        if routing is None:
            raise RoutingError(f"Unknown task type: {task_type!r}")

        alias = routing.model
        model_config = self._models_config.models.get(alias)
        if model_config is None:
            raise RoutingError(
                f"Model alias {alias!r} (for task type {task_type!r}) not found in config"
            )

        provider = self._providers.get(model_config.provider)
        if provider is None:
            raise RoutingError(
                f"Provider {model_config.provider!r} not available "
                f"(alias {alias!r}, task type {task_type!r})"
            )

        return provider, model_config.model, alias

    async def complete(
        self,
        prompt: str,
        task_type: str,
        task_id: str | None = None,
        user_id: str = "system",
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        """Route a completion call through the configured provider.

        Args:
            prompt: Fully-rendered prompt text.
            task_type: Key from task_types.yaml / routing config.
            task_id: Optional associated task ID for logging.
            user_id: User making the request; used by BudgetGuard checks.

        Returns:
            Tuple of (parsed JSON dict, CompletionMetadata).

        Raises:
            RoutingError: If the task type or model cannot be resolved.
            ContextOverflowError: If the prompt exceeds the local budget and
                no fallback is configured.
            BudgetPausedError: If daily spend exceeds the pause threshold.
        """
        if self._budget_guard is not None:
            await self._budget_guard.check_pre_call(user_id)

        provider, model_id, alias = self._resolve_route(task_type)
        model_config = self._models_config.models[alias]

        estimated_in: int | None = None
        overflow_escalated = False
        num_ctx_to_send: int | None = None

        if model_config.provider == "ollama":
            num_ctx_to_send = (
                model_config.num_ctx
                if model_config.num_ctx is not None
                else self._models_config.ollama.default_num_ctx
            )
            output_reserve = self._models_config.ollama.default_output_reserve
            budget = num_ctx_to_send - output_reserve
            estimated_in = estimate_tokens(prompt)

            if estimated_in > budget:
                routing_entry = self._models_config.routing.get(task_type)
                fallback_alias = routing_entry.fallback if routing_entry else None

                if fallback_alias is None:
                    logger.error(
                        "context_overflow_no_fallback",
                        task_type=task_type,
                        from_alias=alias,
                        estimated_tokens=estimated_in,
                        budget=budget,
                        user_id=user_id,
                    )
                    raise ContextOverflowError(
                        f"Prompt for task_type={task_type!r} estimated at "
                        f"{estimated_in} tokens exceeds budget {budget} "
                        f"(alias={alias!r}, num_ctx={num_ctx_to_send}, "
                        f"reserve={output_reserve}); no fallback configured."
                    )

                logger.warning(
                    "context_overflow_escalation",
                    task_type=task_type,
                    from_alias=alias,
                    to_alias=fallback_alias,
                    estimated_tokens=estimated_in,
                    budget=budget,
                    user_id=user_id,
                )

                # Post-resolution validation: these catch config drift (YAML fallback
                # pointing at a missing alias, or a provider whose constructor was never
                # registered), not logic bugs. Keep both.
                fallback_config = self._models_config.models.get(fallback_alias)
                if fallback_config is None:
                    raise RoutingError(
                        f"Fallback alias {fallback_alias!r} (for task type "
                        f"{task_type!r}) not found in config"
                    )
                fallback_provider = self._providers.get(fallback_config.provider)
                if fallback_provider is None:
                    raise RoutingError(
                        f"Fallback provider {fallback_config.provider!r} "
                        f"not available (alias {fallback_alias!r})"
                    )

                provider = fallback_provider
                model_id = fallback_config.model
                alias = fallback_alias
                model_config = fallback_config
                num_ctx_to_send = None  # fallback is not Ollama
                overflow_escalated = True

        logger.info(
            "model_router_dispatch",
            task_type=task_type,
            model_alias=alias,
            model_id=model_id,
            task_id=task_id,
            estimated_tokens_in=estimated_in,
            overflow_escalated=overflow_escalated,
        )

        result, metadata = await resilient_call(
            provider.complete,
            prompt,
            model_id,
            category=TaskCategory.STANDARD,
            circuit_breaker=self._circuit_breaker,
            num_ctx=num_ctx_to_send,
        )

        enriched_metadata = CompletionMetadata(
            latency_ms=metadata.latency_ms,
            tokens_in=metadata.tokens_in,
            tokens_out=metadata.tokens_out,
            cost_usd=metadata.cost_usd,
            model_actual=metadata.model_actual,
            is_shadow=metadata.is_shadow,
            estimated_tokens_in=estimated_in,
            overflow_escalated=overflow_escalated,
        )

        # Shadow mode: fire secondary model in parallel if configured.
        routing = self._models_config.routing.get(task_type)
        if routing and routing.shadow and self._on_shadow_complete:
            asyncio.create_task(
                self._run_shadow(prompt, task_type, routing.shadow)
            )

        return result, enriched_metadata

    async def _run_shadow(
        self, prompt: str, task_type: str, shadow_alias: str
    ) -> None:
        """Run a shadow model call (fire-and-forget, never blocks primary)."""
        try:
            model_config = self._models_config.models.get(shadow_alias)
            if model_config is None:
                logger.warning("shadow_alias_not_found", alias=shadow_alias)
                return

            provider = self._providers.get(model_config.provider)
            if provider is None:
                logger.warning("shadow_provider_not_found", provider=model_config.provider)
                return

            result, metadata = await provider.complete(prompt, model_config.model)
            shadow_metadata = CompletionMetadata(
                latency_ms=metadata.latency_ms,
                tokens_in=metadata.tokens_in,
                tokens_out=metadata.tokens_out,
                cost_usd=metadata.cost_usd,
                model_actual=metadata.model_actual,
                is_shadow=True,
            )

            if self._on_shadow_complete:
                await self._on_shadow_complete(task_type, result, shadow_metadata)

            logger.info(
                "shadow_completion",
                task_type=task_type,
                shadow_alias=shadow_alias,
                latency_ms=shadow_metadata.latency_ms,
            )
        except Exception:
            logger.exception("shadow_completion_failed", task_type=task_type)

    def get_prompt_template(self, task_type: str) -> str:
        """Load and cache the prompt template for a task type."""
        if task_type in self._prompt_cache:
            return self._prompt_cache[task_type]

        tt = self._task_types_config.task_types.get(task_type)
        if tt is None:
            raise RoutingError(f"Unknown task type: {task_type!r}")

        path = self._project_root / tt.prompt_template
        template = path.read_text()
        self._prompt_cache[task_type] = template
        return template

    def get_output_schema(self, task_type: str) -> dict[str, Any]:
        """Load and cache the output JSON schema for a task type."""
        if task_type in self._schema_cache:
            return self._schema_cache[task_type]

        tt = self._task_types_config.task_types.get(task_type)
        if tt is None:
            raise RoutingError(f"Unknown task type: {task_type!r}")

        path = self._project_root / tt.output_schema
        with open(path) as f:
            schema = json.load(f)
        self._schema_cache[task_type] = schema
        return schema
