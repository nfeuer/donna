"""Tests for the Model-Layer Phase-2 hardening slice.

Covers the deferred router.py-heavy findings from the Fable critique design
(``docs/superpowers/specs/2026-06-11-model-layer-fable-critique-design.md``):

- #5 per-provider circuit breaker (an open Ollama breaker must not short-circuit
  Anthropic calls);
- #7 token-truncation tripwire + self-calibrating divisor (design A);
- #2 log billed calls on every error/retry path;
- #3 shadow mode through ``complete()`` with a config kill-switch (design B).

Cites ``spec_v3.md §4`` (Model Abstraction & Evaluation Layer).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import (
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
    RoutingEntry,
    ShadowConfig,
    TaskTypeEntry,
    TaskTypesConfig,
    TokenEstimationConfig,
)
from donna.models.providers._parsing import ResponseParseError
from donna.models.router import ContextOverflowError, ModelRouter
from donna.models.types import CompletionMetadata
from donna.resilience.retry import (
    CircuitBreaker,
    CircuitBreakerState,
    TaskCategory,
    is_transient_error,
    resilient_call,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Recording invocation logger
# ---------------------------------------------------------------------------


class _RecordingLogger:
    """Captures every InvocationMetadata passed to ``log``."""

    def __init__(self) -> None:
        self.rows: list[Any] = []
        self._conn = MagicMock()

    async def log(self, metadata: Any) -> str:
        self.rows.append(metadata)
        return f"inv-{len(self.rows)}"


def _meta(
    *,
    tokens_in: int = 50,
    tokens_out: int = 20,
    cost: float = 0.001,
    model_actual: str = "anthropic/claude-sonnet-4-6",
    token_limited: bool = False,
) -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=10,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        model_actual=model_actual,
        token_limited=token_limited,
    )


# ---------------------------------------------------------------------------
# #5 — per-provider circuit breaker
# ---------------------------------------------------------------------------


def _mixed_models_config(
    *, shadow_enabled: bool = False, shadow_alias: str | None = None
) -> ModelsConfig:
    """Ollama-primary task with an Anthropic fallback + an Anthropic-only task."""
    routing = {
        "summarize": RoutingEntry(
            model="local",
            fallback="cloud",
            shadow=shadow_alias,
        ),
        "cloud_task": RoutingEntry(model="cloud"),
    }
    return ModelsConfig(
        models={
            "local": ModelConfig(
                provider="ollama",
                model="qwen2.5:32b",
                num_ctx=8192,
                estimated_cost_per_1k_tokens=0.0001,
            ),
            "cloud": ModelConfig(
                provider="anthropic",
                model="claude-sonnet-4-6",
                input_cost_per_token_usd=0.000003,
                output_cost_per_token_usd=0.000015,
            ),
        },
        routing=routing,
        ollama=OllamaConfig(default_num_ctx=8192, default_output_reserve=1024),
        shadow=ShadowConfig(enabled=shadow_enabled),
    )


def _task_types() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "summarize": TaskTypeEntry(
                description="",
                model="local",
                prompt_template="unused.md",
                output_schema="unused.json",
            ),
            "cloud_task": TaskTypeEntry(
                description="",
                model="cloud",
                prompt_template="unused.md",
                output_schema="unused.json",
            ),
        }
    )


def _build_mixed_router(
    *, shadow_enabled: bool = False, shadow_alias: str | None = None
) -> tuple[ModelRouter, MagicMock, MagicMock, _RecordingLogger]:
    models = _mixed_models_config(
        shadow_enabled=shadow_enabled, shadow_alias=shadow_alias
    )
    logger = _RecordingLogger()
    router = ModelRouter(
        models, _task_types(), PROJECT_ROOT, invocation_logger=logger
    )
    ollama = MagicMock()
    ollama.complete = AsyncMock()
    anthropic = MagicMock()
    anthropic.complete = AsyncMock(return_value=({"ok": True}, _meta()))
    router._providers["ollama"] = ollama
    router._providers["anthropic"] = anthropic
    return router, ollama, anthropic, logger


@pytest.mark.asyncio
async def test_open_ollama_breaker_does_not_block_anthropic() -> None:
    """An Ollama outage opening Ollama's breaker must NOT short-circuit cloud.

    Regression for critique #5: a single shared breaker coupled the providers,
    so a local-GPU outage blacked out Anthropic calls.
    """
    router, _ollama, anthropic, _logger = _build_mixed_router()

    # Force Ollama's breaker open as if a local outage already occurred.
    ollama_breaker = router._breaker_for("ollama")
    ollama_breaker.state = CircuitBreakerState.OPEN
    ollama_breaker.failure_count = 99

    # A cloud-only task must still go through — it consults the Anthropic breaker.
    result, meta = await router.complete(prompt="hi", task_type="cloud_task")
    assert result == {"ok": True}
    assert meta.model_actual.startswith("anthropic/")
    anthropic.complete.assert_awaited_once()
    # Ollama's breaker is still open; Anthropic's is independent and closed.
    assert router._breaker_for("ollama").state is CircuitBreakerState.OPEN
    assert router._breaker_for("anthropic").state is CircuitBreakerState.CLOSED


@pytest.mark.asyncio
async def test_breaker_target_is_fallback_provider_after_overflow() -> None:
    """After overflow escalation the breaker consulted is the FALLBACK's.

    The prompt overflows the Ollama budget and escalates to the Anthropic
    fallback, so the call must consult Anthropic's breaker (not Ollama's).
    Opening Ollama's breaker first proves the escalated call still runs.
    """
    router, _ollama, anthropic, _logger = _build_mixed_router()
    router._breaker_for("ollama").state = CircuitBreakerState.OPEN

    large_prompt = "x" * 200_000  # comfortably overflows num_ctx budget
    _result, meta = await router.complete(prompt=large_prompt, task_type="summarize")
    assert meta.overflow_escalated is True
    assert meta.model_actual.startswith("anthropic/")
    anthropic.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_breaker_open_dispatches_fallback_alert() -> None:
    """When a provider's breaker opens, a fallback alert is dispatched."""
    router, _ollama, anthropic, _logger = _build_mixed_router()
    alert = AsyncMock(return_value=True)
    router.set_fallback_alert_fn(alert)

    # Pre-load Anthropic's breaker to one failure below the threshold so the
    # next failing call opens it during resilient_call.
    breaker = router._breaker_for("anthropic")
    breaker.failure_count = breaker.failure_threshold - 1
    breaker.first_failure_time = __import__("time").monotonic()

    # Make the cloud provider fail with a retryable error so the breaker trips.
    anthropic.complete = AsyncMock(side_effect=ConnectionError("boom"))

    with pytest.raises(ConnectionError):
        await router.complete(prompt="hi", task_type="cloud_task")

    assert breaker.state is CircuitBreakerState.OPEN
    # A breaker-open alert was dispatched (component=model_router).
    assert any(
        "Circuit breaker opened" in c.kwargs.get("error", "")
        for c in alert.await_args_list
    )


# ---------------------------------------------------------------------------
# #7 — token tripwire + self-calibrating divisor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncation_tripwire_redispatches_to_fallback() -> None:
    """A saturated Ollama window (tokens_in≥budget) re-dispatches to fallback.

    The local call returns tokens_in at the budget edge (suspected silent
    truncation); the router alerts and re-runs on the cloud fallback.
    """
    router, ollama, anthropic, logger = _build_mixed_router()
    alert = AsyncMock(return_value=True)
    router.set_fallback_alert_fn(alert)

    # num_ctx=8192, reserve=1024 → budget 7168. Return tokens_in at the edge so
    # the prompt is short enough to skip the PRE-call overflow path but the
    # POST-call tripwire fires.
    ollama.complete = AsyncMock(
        return_value=(
            {"local": True},
            _meta(tokens_in=7168, model_actual="ollama/qwen2.5:32b"),
        )
    )

    result, meta = await router.complete(prompt="short prompt", task_type="summarize")

    # Re-dispatched to the cloud fallback.
    assert result == {"ok": True}
    assert meta.model_actual.startswith("anthropic/")
    assert meta.overflow_escalated is True
    anthropic.complete.assert_awaited_once()
    # truncation alert dispatched.
    assert any(
        "silent truncation" in c.kwargs.get("error", "").lower()
        for c in alert.await_args_list
    )
    # The suspect-but-billed local call was still logged (interrupted), plus the
    # successful fallback call.
    assert any(r.interrupted for r in logger.rows)
    assert any(not r.interrupted for r in logger.rows)


@pytest.mark.asyncio
async def test_truncation_tripwire_loud_fails_without_fallback() -> None:
    """Saturated window + no fallback → loud ContextOverflowError (KEEP)."""
    models = _mixed_models_config()
    # Drop the fallback from the summarize route.
    models.routing["summarize"] = RoutingEntry(model="local")
    logger = _RecordingLogger()
    router = ModelRouter(models, _task_types(), PROJECT_ROOT, invocation_logger=logger)
    ollama = MagicMock()
    ollama.complete = AsyncMock(
        return_value=(
            {"local": True},
            _meta(tokens_in=7168, model_actual="ollama/qwen2.5:32b"),
        )
    )
    router._providers["ollama"] = ollama

    with pytest.raises(ContextOverflowError, match="truncation"):
        await router.complete(prompt="short", task_type="summarize")
    # The billed (suspect) local call is still logged before the raise.
    assert any(r.interrupted for r in logger.rows)


@pytest.mark.asyncio
async def test_divisor_ema_self_calibrates() -> None:
    """A healthy Ollama call feeds the observed ratio into the per-task EMA."""
    router, ollama, _anthropic, _logger = _build_mixed_router()
    prompt = "x" * 1000
    # Report tokens_in that implies ratio 1000/400 = 2.5 (clamped low bound).
    ollama.complete = AsyncMock(
        return_value=({"ok": True}, _meta(tokens_in=400, model_actual="ollama/q"))
    )

    assert "summarize" not in router._token_divisors
    await router.complete(prompt=prompt, task_type="summarize")
    # EMA now seeded toward the observed (clamped) ratio.
    assert router._token_divisors["summarize"] == pytest.approx(2.5, abs=0.01)


def test_token_divisor_respects_bounds_and_safety_factor() -> None:
    cfg = TokenEstimationConfig(
        safety_factor=0.5, ema_alpha=0.2, divisor_bounds=(2.5, 4.5)
    )
    models = _mixed_models_config()
    models.ollama.token_estimation = cfg
    router = ModelRouter(
        models, _task_types(), PROJECT_ROOT, invocation_logger=_RecordingLogger()
    )
    # Fresh divisor = midpoint 3.5 * 0.5 = 1.75 → clamped up to low bound 2.5.
    assert router._token_divisor_for("summarize") == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# #2 — log billed calls on every error/retry path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_failure_then_success_logs_two_rows() -> None:
    """A parse failure (retryable once) then success logs 2 invocation rows."""
    router, _ollama, anthropic, logger = _build_mixed_router()

    good_meta = _meta(model_actual="anthropic/claude-sonnet-4-6")
    parse_err = ResponseParseError(
        "bad json", metadata=_meta(tokens_in=42, cost=0.002)
    )
    anthropic.complete = AsyncMock(
        side_effect=[parse_err, ({"ok": True}, good_meta)]
    )

    result, _meta_out = await router.complete(prompt="hi", task_type="cloud_task")
    assert result == {"ok": True}

    assert len(logger.rows) == 2
    failed = [r for r in logger.rows if r.interrupted]
    succeeded = [r for r in logger.rows if not r.interrupted]
    assert len(failed) == 1
    assert len(succeeded) == 1
    # The failed attempt carried the provider's pre-parse usage.
    assert failed[0].tokens_in == 42
    assert failed[0].cost_usd == 0.002


@pytest.mark.asyncio
async def test_parse_failure_retries_at_most_once_and_logs_each_attempt() -> None:
    """A persistent parse failure retries AT MOST ONCE (2 billed attempts).

    A model that returns garbage JSON rarely self-corrects on a third try, so
    parse failures are capped at one retry — but every billed attempt is still
    logged (model-layer critique #2).
    """
    router, _ollama, anthropic, logger = _build_mixed_router()
    anthropic.complete = AsyncMock(
        side_effect=ResponseParseError("bad", metadata=_meta(tokens_in=7))
    )

    with pytest.raises(ResponseParseError):
        await router.complete(prompt="hi", task_type="cloud_task")

    # 1 initial + 1 retry = 2 billed attempts, all logged + interrupted.
    assert anthropic.complete.await_count == 2
    assert len(logger.rows) == 2
    assert all(r.interrupted for r in logger.rows)
    assert all(r.tokens_in == 7 for r in logger.rows)


@pytest.mark.asyncio
async def test_transport_error_retries_log_each_billed_attempt() -> None:
    """A transient error that DOES carry usage logs every billed attempt.

    Uses a billed metadata-bearing transport-style error to prove the
    per-attempt logging spans the full STANDARD retry budget (3 attempts) for
    genuinely transient failures (unlike the parse-failure cap above).
    """

    class _BilledTransientError(Exception):
        def __init__(self) -> None:
            super().__init__("overloaded")
            self.status_code = 529
            self.metadata = _meta(tokens_in=5)

    router, _ollama, anthropic, logger = _build_mixed_router()
    anthropic.complete = AsyncMock(side_effect=_BilledTransientError())

    with pytest.raises(_BilledTransientError):
        await router.complete(prompt="hi", task_type="cloud_task")

    # STANDARD = 1 initial + 2 retries = 3 billed attempts, each logged.
    assert anthropic.complete.await_count == 3
    assert len(logger.rows) == 3
    assert all(r.interrupted for r in logger.rows)


@pytest.mark.asyncio
async def test_non_retryable_error_is_not_retried() -> None:
    """A 4xx/auth-style error fails fast (one attempt) and is not retried."""

    class _AuthError(Exception):
        status_code = 401

    router, _ollama, anthropic, _logger = _build_mixed_router()
    anthropic.complete = AsyncMock(side_effect=_AuthError("unauthorized"))

    with pytest.raises(_AuthError):
        await router.complete(prompt="hi", task_type="cloud_task")
    # Exactly one call — no retries for a terminal client error.
    assert anthropic.complete.await_count == 1


@pytest.mark.asyncio
async def test_transport_error_without_usage_logs_no_billed_row() -> None:
    """A transport error before any usage carries no metadata → no billed row."""
    router, _ollama, anthropic, logger = _build_mixed_router()
    anthropic.complete = AsyncMock(side_effect=ConnectionError("down"))

    with pytest.raises(ConnectionError):
        await router.complete(prompt="hi", task_type="cloud_task")
    # No billed usage to record (the call never returned a response body).
    assert logger.rows == []


def test_is_transient_error_classification() -> None:
    assert is_transient_error(ConnectionError("x")) is True
    assert is_transient_error(TimeoutError()) is True

    class _StatusError(Exception):
        def __init__(self, code: int) -> None:
            self.status_code = code

    assert is_transient_error(_StatusError(529)) is True   # Anthropic overloaded
    assert is_transient_error(_StatusError(503)) is True
    assert is_transient_error(_StatusError(429)) is True
    assert is_transient_error(_StatusError(401)) is False  # auth — terminal
    assert is_transient_error(_StatusError(400)) is False  # bad request — terminal
    # Unknown shape defaults to retryable (never make transient terminal).
    assert is_transient_error(ValueError("?")) is True


# ---------------------------------------------------------------------------
# #3 — shadow mode through complete() + kill switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_disabled_does_not_fire() -> None:
    """With shadow.enabled=False, a configured shadow alias never runs."""
    router, ollama, _anthropic, logger = _build_mixed_router(
        shadow_enabled=False, shadow_alias="cloud"
    )
    ollama.complete = AsyncMock(
        return_value=({"ok": True}, _meta(tokens_in=10, model_actual="ollama/q"))
    )
    await router.complete(prompt="short", task_type="summarize")
    # Only the primary call logged; no shadow task spawned.
    assert router._shadow_tasks == set()
    assert all(not r.is_shadow for r in logger.rows)


@pytest.mark.asyncio
async def test_shadow_enabled_routes_through_complete_and_is_accounted() -> None:
    """An enabled shadow runs through complete() and is logged is_shadow=1.

    Design B / critique #3: shadow spend must land on invocation_log with its
    cost, and a shadow call must never spawn its own shadow.
    """
    router, ollama, anthropic, logger = _build_mixed_router(
        shadow_enabled=True, shadow_alias="cloud"
    )
    ollama.complete = AsyncMock(
        return_value=({"ok": True}, _meta(tokens_in=10, model_actual="ollama/q"))
    )
    # Shadow (cloud) returns a distinct billed metadata.
    anthropic.complete = AsyncMock(
        return_value=({"shadow": True}, _meta(cost=0.005))
    )

    await router.complete(prompt="short", task_type="summarize")

    # Drain the fire-and-forget shadow task(s).
    import asyncio

    pending = list(router._shadow_tasks)
    assert pending, "a shadow task should have been spawned"
    await asyncio.gather(*pending)

    shadow_rows = [r for r in logger.rows if r.is_shadow]
    primary_rows = [r for r in logger.rows if not r.is_shadow]
    assert len(primary_rows) >= 1
    assert len(shadow_rows) == 1
    # Shadow spend is accounted with its real cost.
    assert shadow_rows[0].cost_usd == 0.005
    # The shadow did not recurse into another shadow.
    assert len(shadow_rows) == 1


@pytest.mark.asyncio
async def test_shadow_resolution_is_prefix_aware() -> None:
    """Shadow resolves via _lookup_routing_entry, so prefixed task_types work."""
    models = _mixed_models_config(shadow_enabled=True, shadow_alias="cloud")
    logger = _RecordingLogger()
    router = ModelRouter(models, _task_types(), PROJECT_ROOT, invocation_logger=logger)
    ollama = MagicMock()
    ollama.complete = AsyncMock(
        return_value=({"ok": True}, _meta(tokens_in=10, model_actual="ollama/q"))
    )
    anthropic = MagicMock()
    anthropic.complete = AsyncMock(return_value=({"shadow": True}, _meta(cost=0.003)))
    router._providers["ollama"] = ollama
    router._providers["anthropic"] = anthropic

    # A dynamic prefixed task_type that resolves to "summarize" via prefix match.
    await router.complete(prompt="short", task_type="summarize::variantA")

    import asyncio

    await asyncio.gather(*list(router._shadow_tasks))
    assert any(r.is_shadow for r in logger.rows)


# ---------------------------------------------------------------------------
# resilient_call hook wiring (unit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resilient_call_invokes_attempt_hook_per_failure() -> None:
    seen: list[tuple[int, bool]] = []

    async def hook(exc: Exception, attempt: int, will_retry: bool) -> None:
        seen.append((attempt, will_retry))

    async def always_fail() -> str:
        raise ConnectionError("x")

    with pytest.raises(ConnectionError):
        await resilient_call(
            always_fail,
            category=TaskCategory.STANDARD,  # 1 + 2 retries
            on_attempt_failure=hook,
        )
    # 3 attempts: first two will_retry=True, last False.
    assert seen == [(0, True), (1, True), (2, False)]


@pytest.mark.asyncio
async def test_resilient_call_non_retryable_skips_remaining_attempts() -> None:
    calls = 0

    async def fail() -> str:
        nonlocal calls
        calls += 1
        err = ValueError("terminal")
        raise err

    with pytest.raises(ValueError):
        await resilient_call(
            fail,
            category=TaskCategory.CRITICAL,  # would be 1+3 retries
            is_retryable=lambda e: False,
        )
    assert calls == 1


def test_per_attempt_failure_records_open_breaker() -> None:
    cb = CircuitBreaker(failure_threshold=2, window_s=600)
    cb.record_failure()
    assert cb.state is CircuitBreakerState.CLOSED
    cb.record_failure()
    assert cb.state is CircuitBreakerState.OPEN
