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
from typing import TYPE_CHECKING, Any, cast

import structlog

from donna.config import ModelsConfig, TaskTypesConfig
from donna.models.providers import ModelProvider
from donna.models.providers.anthropic import AnthropicProvider
from donna.models.tokens import estimate_tokens
from donna.models.types import CompletionMetadata
from donna.resilience.retry import CircuitBreaker, TaskCategory, resilient_call

if TYPE_CHECKING:
    from donna.collection.payload_writer import PayloadWriter
    from donna.cost.budget import BudgetGuard
    from donna.cost.escalation_gate import EscalationGate
    from donna.logging.invocation_logger import InvocationLogger

logger = structlog.get_logger()


class EscalationDecisionError(Exception):
    """Raised by ``complete()`` when the over-budget gate resolves to a
    terminal mode that *replaces* the autonomous API call.

    Modes that raise:
    - ``pause`` / ``cancel`` — task should not run today (slice 17).
    - ``claude_code`` / ``chat`` — user is doing the work manually
      (slices 20 / 21); the result lands later via the dashboard
      submit + poller path. The caller is expected to leave the
      originating record (e.g. ``skill_candidate_report`` row) in a
      state the poller can update on success.

    Carries the resolution mode + the ``escalation_request_id`` so the
    caller can stamp follow-up audit rows. See
    docs/superpowers/specs/manual-escalation.md §4 / §5.2 / §5.3."""

    def __init__(
        self, *, mode: str, escalation_request_id: int, correlation_id: str
    ) -> None:
        self.mode = mode
        self.escalation_request_id = escalation_request_id
        self.correlation_id = correlation_id
        super().__init__(
            f"Escalation resolved as {mode!r} "
            f"(request_id={escalation_request_id})"
        )


class TokenLimitReachedError(Exception):
    """Raised by ``complete()`` when the provider truncated its output at the
    extension-derived token cap (§10.6 row 1).

    The caller should re-estimate the task and re-offer escalation so the
    user can approve a larger extension rather than receiving a silently
    truncated result.
    """

    def __init__(
        self, *, escalation_request_id: int, correlation_id: str
    ) -> None:
        self.escalation_request_id = escalation_request_id
        self.correlation_id = correlation_id
        super().__init__(
            f"Token limit reached for api_extended call "
            f"(request_id={escalation_request_id}). Re-escalation required."
        )


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
        escalation_gate: EscalationGate | None = None,
        invocation_logger: InvocationLogger | None = None,
        payload_writer: PayloadWriter | None = None,
        fallback_alert_fn: Callable[..., Awaitable[bool]] | None = None,
    ) -> None:
        self._models_config = models_config
        self._task_types_config = task_types_config
        self._project_root = project_root
        self._budget_guard = budget_guard
        self._on_shadow_complete = on_shadow_complete
        self._escalation_gate = escalation_gate
        self._invocation_logger = invocation_logger
        self._payload_writer = payload_writer
        self._fallback_alert_fn = fallback_alert_fn
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
        # Strong references to fire-and-forget shadow tasks so they are
        # not garbage-collected before completion.
        self._shadow_tasks: set[asyncio.Task[None]] = set()
        # True while Ollama has fallen back to the cloud provider due to a
        # context-overflow escalation; reset to False on the next successful
        # Ollama call (recovery detection below).
        self._ollama_degraded = False

    def set_escalation_gate(self, gate: EscalationGate | None) -> None:
        """Late-bind the over-budget escalation gate.

        Slice 17 wires the gate after the Discord bot is constructed
        (the gate's delivery callback needs the bot), but the router
        is built earlier in the boot sequence.
        """
        self._escalation_gate = gate

    def set_fallback_alert_fn(
        self, fn: Callable[..., Awaitable[bool]] | None
    ) -> None:
        """Late-bind the fallback alert callback.

        The notification service is constructed after the router in the
        boot sequence, so this is wired once the service exists.
        """
        self._fallback_alert_fn = fn

    def _lookup_routing_entry(self, task_type: str) -> Any | None:
        """Lookup routing config by exact key, then longest-prefix match."""
        routing = self._models_config.routing.get(task_type)
        if routing is None:
            parts = task_type.split("::")
            for i in range(len(parts) - 1, 0, -1):
                candidate = "::".join(parts[:i])
                routing = self._models_config.routing.get(candidate)
                if routing is not None:
                    break
        return routing

    def _resolve_route(self, task_type: str) -> tuple[ModelProvider, str, str]:
        """Resolve task_type → (provider instance, model ID, model alias).

        Exact-key match takes precedence. When no exact key matches, fall back
        to longest-prefix match on the "::"-separated task_type. Lets callers
        pass dynamic task_types like ``skill_step::<cap>::<step>`` without
        registering every combination in donna_models.yaml.

        Raises RoutingError if neither exact nor any prefix match.
        """
        routing = self._lookup_routing_entry(task_type)
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
        estimate_usd: float | None = None,
        priority: int = 2,
        originating_entity: tuple[str, str] | None = None,
        target_paths: dict[str, str] | None = None,
        base_sha: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        """Route a completion call through the configured provider.

        Args:
            prompt: Fully-rendered prompt text.
            task_type: Key from task_types.yaml / routing config.
            task_id: Optional associated task ID for logging.
            user_id: User making the request; used by BudgetGuard checks.
            estimate_usd: Pre-flight cost estimate. When provided alongside
                a configured escalation gate, the gate decides whether to
                offer the user a Discord choice instead of spending.
                When omitted (the default for slice 17 callers), behaviour
                is unchanged.
            priority: Task priority (1–5). Forwarded to the gate for
                tier-2 SMS fallback on timeout.
            originating_entity: Slice 21. ``(entity_type, entity_id)``
                tuple identifying the row that triggered the call (e.g.
                ``('skill_candidate_report', candidate.id)``). Threaded
                to the gate so the claude_code diff validator can render
                ``{name}``-substituted target_paths globs without
                inferring identity from a NULL ``task_id``.
            target_paths: Slice 21. Optional pre-rendered glob dict to
                snapshot on the escalation_request row. When omitted,
                the gate may render from ``task_types.yaml`` itself.
            base_sha: Slice 21. Pinned ``main`` SHA captured at the
                caller side (or by the gate); persisted on the row so
                the worktree command stays reproducible.

        Returns:
            Tuple of (parsed JSON dict, CompletionMetadata).

        Raises:
            RoutingError: If the task type or model cannot be resolved.
            ContextOverflowError: If the prompt exceeds the local budget and
                no fallback is configured.
            BudgetPausedError: If daily spend exceeds the pause threshold.
            EscalationDecision: If the over-budget gate resolved the
                request to ``pause`` or ``cancel`` (slice 17). Caller is
                responsible for transitioning the task to the matching
                terminal state.
        """
        # Track escalation context for invocation logging and token-limit
        # enforcement after the gate is consulted.
        _escalation_request_id: int | None = None
        _escalation_correlation_id: str | None = None
        _max_tokens_override: int | None = None
        _extension_amount_usd: float | None = None

        if (
            self._escalation_gate is not None
            and estimate_usd is not None
        ):
            outcome = await self._escalation_gate.fire_and_wait(
                user_id=user_id,
                task_id=task_id,
                task_type=task_type,
                estimate_usd=estimate_usd,
                priority=priority,
                originating_entity=originating_entity,
                target_paths=target_paths,
                base_sha=base_sha,
                # Slice 20 — pass the rendered prompt so the gate can
                # offer chat mode and persist the prompt body for the
                # dashboard / Discord attachment.
                original_prompt=prompt,
            )
            # ``pause``, ``cancel``, ``chat`` (slice 20), and ``claude_code``
            # (slice 21) all mean "no autonomous API call". The caller
            # catches the exception and parks the task. For chat /
            # claude_code, the relevant submit-poller path will land the
            # result once the user submits manually — falling through
            # here would charge the budget for a request the user is
            # replacing.
            if outcome.fired and outcome.mode in (
                "pause", "cancel", "chat", "claude_code",
            ):
                assert outcome.escalation_request_id is not None
                assert outcome.correlation_id is not None
                raise EscalationDecisionError(
                    mode=outcome.mode,
                    escalation_request_id=outcome.escalation_request_id,
                    correlation_id=outcome.correlation_id,
                )
            if outcome.fired and outcome.mode == "api_extended":
                _escalation_request_id = outcome.escalation_request_id
                _escalation_correlation_id = outcome.correlation_id
                # Derive max_tokens from the extension amount so actual spend
                # cannot silently exceed the approved budget (§10.6 row 1).
                # Token rate comes from config/donna_models.yaml per alias;
                # route resolution happens below, so we defer the calculation.
                # Store the extension amount; the cap is applied after routing.
                _extension_amount_usd = outcome.extension_amount_usd

        if self._budget_guard is not None:
            await self._budget_guard.check_pre_call(user_id)

        provider, model_id, alias = self._resolve_route(task_type)
        original_alias = alias
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
                routing_entry = self._lookup_routing_entry(task_type)
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

                if not self._ollama_degraded:
                    self._ollama_degraded = True
                    logger.warning(
                        "ollama_fallback_activated",
                        event_type="system.ollama_fallback",
                        task_type=task_type,
                        from_alias=original_alias,
                        to_alias=fallback_alias,
                    )

                if self._fallback_alert_fn is not None:
                    try:
                        await self._fallback_alert_fn(
                            component="model_router",
                            error=(
                                f"Context overflow: {estimated_in} tokens"
                                f" > {budget} budget for"
                                f" {original_alias!r}"
                            ),
                            fallback=f"escalated to {fallback_alias!r}",
                            context={
                                "task_type": task_type,
                                "from_alias": original_alias,
                                "to_alias": fallback_alias,
                            },
                        )
                    except Exception:
                        logger.warning("fallback_alert_fn_failed", task_type=task_type)

        # Compute token limit so total spend (input + output) cannot exceed
        # the approved extension. §10.6 row 1 says "extension_amount × token_rate";
        # in practice both prompt input and generated output are billed, so we
        # reserve input cost first and let max_tokens cap the remainder.
        # If the prompt's input cost alone exhausts the extension, raise so the
        # caller re-estimates rather than burning the budget on input only.
        if _escalation_request_id is not None and _extension_amount_usd is not None:
            output_cost = model_config.output_cost_per_token_usd
            input_cost = model_config.input_cost_per_token_usd
            if output_cost and output_cost > 0:
                input_tokens = estimated_in if estimated_in is not None else estimate_tokens(prompt)
                input_spend = (input_tokens * input_cost) if input_cost else 0.0
                remaining_budget = _extension_amount_usd - input_spend
                if remaining_budget <= 0:
                    assert _escalation_correlation_id is not None
                    logger.warning(
                        "model_router_extension_input_exhausts_budget",
                        extension_amount_usd=_extension_amount_usd,
                        input_tokens=input_tokens,
                        input_cost_per_token=input_cost,
                        input_spend=input_spend,
                        escalation_request_id=_escalation_request_id,
                    )
                    raise TokenLimitReachedError(
                        escalation_request_id=_escalation_request_id,
                        correlation_id=_escalation_correlation_id,
                    )
                _max_tokens_override = max(1, int(remaining_budget / output_cost))
                logger.info(
                    "model_router_extension_token_limit",
                    max_tokens=_max_tokens_override,
                    extension_amount_usd=_extension_amount_usd,
                    input_tokens=input_tokens,
                    input_spend=input_spend,
                    remaining_budget_for_output=remaining_budget,
                    output_cost_per_token=output_cost,
                    escalation_request_id=_escalation_request_id,
                )
            else:
                logger.warning(
                    "model_router_no_output_cost_rate",
                    alias=alias,
                    task_type=task_type,
                )

        logger.info(
            "model_router_dispatch",
            task_type=task_type,
            model_alias=alias,
            model_id=model_id,
            task_id=task_id,
            estimated_tokens_in=estimated_in,
            overflow_escalated=overflow_escalated,
            escalation_request_id=_escalation_request_id,
        )

        call_kwargs: dict[str, Any] = {"num_ctx": num_ctx_to_send}
        if _max_tokens_override is not None:
            call_kwargs["max_tokens"] = _max_tokens_override
        if tools is not None:
            call_kwargs["tools"] = tools
        if messages is not None:
            call_kwargs["messages"] = messages

        result, metadata = await resilient_call(
            provider.complete,
            prompt,
            model_id,
            category=TaskCategory.STANDARD,
            circuit_breaker=self._circuit_breaker,
            **call_kwargs,
        )

        # Recovery detection: if the call actually went to Ollama (i.e. was not
        # escalated to the cloud fallback) and we previously marked Ollama as
        # degraded, this success means Ollama is back.
        original_model_config = self._models_config.models[original_alias]
        if (
            original_model_config.provider == "ollama"
            and not overflow_escalated
            and self._ollama_degraded
        ):
            self._ollama_degraded = False
            logger.info(
                "ollama_recovered",
                event_type="system.ollama_recovered",
                task_type=task_type,
            )
            if self._fallback_alert_fn is not None:
                try:
                    await self._fallback_alert_fn(
                        component="model_router",
                        error="Ollama recovered — no longer falling back to cloud",
                        fallback="resuming local model routing",
                        context={"task_type": task_type},
                    )
                except Exception:
                    logger.warning("fallback_alert_fn_failed_recovery", task_type=task_type)

        enriched_metadata = CompletionMetadata(
            latency_ms=metadata.latency_ms,
            tokens_in=metadata.tokens_in,
            tokens_out=metadata.tokens_out,
            cost_usd=metadata.cost_usd,
            model_actual=metadata.model_actual,
            is_shadow=metadata.is_shadow,
            estimated_tokens_in=estimated_in,
            overflow_escalated=overflow_escalated,
            token_limited=metadata.token_limited,
        )

        # §10.6 row 1: if the response was cut off by the extension token cap,
        # raise so the caller can re-estimate and re-escalate rather than
        # silently returning a truncated result.
        if metadata.token_limited and _escalation_request_id is not None:
            assert _escalation_correlation_id is not None
            raise TokenLimitReachedError(
                escalation_request_id=_escalation_request_id,
                correlation_id=_escalation_correlation_id,
            )

        # Auto-log every successful LLM call to invocation_log.
        invocation_id: str | None = None
        if self._invocation_logger is not None:
            from donna.logging.invocation_logger import InvocationMetadata

            try:
                invocation_id = await self._invocation_logger.log(
                    InvocationMetadata(
                        task_type=task_type,
                        model_alias=alias,
                        model_actual=enriched_metadata.model_actual,
                        input_hash="",
                        latency_ms=enriched_metadata.latency_ms,
                        tokens_in=enriched_metadata.tokens_in,
                        tokens_out=enriched_metadata.tokens_out,
                        cost_usd=enriched_metadata.cost_usd,
                        estimated_tokens_in=enriched_metadata.estimated_tokens_in,
                        overflow_escalated=enriched_metadata.overflow_escalated,
                        user_id=user_id,
                        task_id=task_id,
                        escalation_request_id=_escalation_request_id,
                    )
                )
            except Exception:
                logger.warning("invocation_log_write_failed", task_type=task_type)

        # Write request/response payload to disk for forensic inspection.
        if self._payload_writer is not None and invocation_id is not None:
            import hashlib

            request_payload = {
                "messages": messages or [{"role": "user", "content": prompt}],
                "model": model_id,
                "tools": tools,
                "max_tokens": call_kwargs.get("max_tokens"),
            }
            response_payload = {
                "content": result,
                "usage": {
                    "input_tokens": enriched_metadata.tokens_in,
                    "output_tokens": enriched_metadata.tokens_out,
                },
                "stop_reason": "end_turn",
                "model_actual": enriched_metadata.model_actual,
            }

            system_text = prompt
            if messages:
                system_parts = [
                    m.get("content", "")
                    for m in messages
                    if m.get("role") == "system"
                ]
                if system_parts:
                    system_text = "\n".join(str(p) for p in system_parts)
            input_hash = hashlib.sha256(system_text.encode()).hexdigest()[:16]

            try:
                rel_path = await self._payload_writer.write(
                    invocation_id=invocation_id,
                    request=request_payload,
                    response=response_payload,
                )
                if rel_path and self._invocation_logger is not None:
                    conn = self._invocation_logger._conn
                    await conn.execute(
                        "UPDATE invocation_log SET payload_path = ?, input_hash = ? WHERE id = ?",
                        (rel_path, input_hash, invocation_id),
                    )
                    await conn.commit()
            except Exception:
                logger.warning("payload_write_failed", task_type=task_type)

        # Shadow mode: fire secondary model in parallel if configured.
        routing = self._models_config.routing.get(task_type)
        if routing and routing.shadow and self._on_shadow_complete:
            shadow_task = asyncio.create_task(
                self._run_shadow(prompt, task_type, routing.shadow)
            )
            self._shadow_tasks.add(shadow_task)
            shadow_task.add_done_callback(self._shadow_tasks.discard)

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
            schema = cast(dict[str, Any], json.load(f))
        self._schema_cache[task_type] = schema
        return schema
