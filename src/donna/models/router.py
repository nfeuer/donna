"""Model router — config-driven routing for all LLM calls.

Loads routing configuration from donna_models.yaml and task_types.yaml,
resolves task_type → model alias → provider, and dispatches completions
through the resilience layer. See docs/model-layer.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from donna.config import ModelsConfig, TaskTypesConfig
from donna.models.providers.anthropic import AnthropicProvider
from donna.models.types import CompletionMetadata
from donna.resilience.retry import CircuitBreaker, TaskCategory, resilient_call

# Imported lazily to avoid circular dependency: budget → tracker → aiosqlite,
# while router is used by dedup which is used by budget.
# Type-only import is sufficient here.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from donna.cost.budget import BudgetGuard

logger = structlog.get_logger()


class RoutingError(Exception):
    """Raised when a task type or model alias cannot be resolved."""


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
    ) -> None:
        self._models_config = models_config
        self._task_types_config = task_types_config
        self._project_root = project_root
        self._budget_guard = budget_guard
        self._circuit_breaker = CircuitBreaker()

        # Instantiate providers. Only anthropic for Phase 1.
        self._providers: dict[str, AnthropicProvider] = {
            "anthropic": AnthropicProvider(),
        }

        # Cache for loaded prompt templates and schemas.
        self._prompt_cache: dict[str, str] = {}
        self._schema_cache: dict[str, dict[str, Any]] = {}

    def _resolve_route(self, task_type: str) -> tuple[AnthropicProvider, str, str]:
        """Resolve task_type → (provider instance, model ID, model alias).

        Raises RoutingError if the task type or alias is unknown.
        """
        routing = self._models_config.routing.get(task_type)
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
            BudgetPausedError: If daily spend exceeds the pause threshold.
        """
        if self._budget_guard is not None:
            await self._budget_guard.check_pre_call(user_id)

        provider, model_id, alias = self._resolve_route(task_type)

        logger.info(
            "model_router_dispatch",
            task_type=task_type,
            model_alias=alias,
            model_id=model_id,
            task_id=task_id,
        )

        result, metadata = await resilient_call(
            provider.complete,
            prompt,
            model_id,
            category=TaskCategory.STANDARD,
            circuit_breaker=self._circuit_breaker,
        )

        return result, metadata

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
