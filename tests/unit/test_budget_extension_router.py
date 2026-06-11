"""Unit tests for ModelRouter token-limit enforcement (slice 18).

Verifies that:
- `max_tokens` is passed to the provider when `api_extended` outcome sets an
  extension amount and the model alias has `output_cost_per_token_usd`.
- `TokenLimitReachedError` is raised when the provider returns
  `token_limited=True` for an escalated call.

Realizes manual-escalation.md §10.6 row 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import (
    CostConfig,
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
    QualityMonitoringConfig,
    RoutingEntry,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.cost.escalation_gate import GateOutcome
from donna.models.router import ModelRouter, TokenLimitReachedError
from donna.models.tokens import estimate_tokens
from donna.models.types import CompletionMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_models_config(
    output_cost_per_token: float | None = 0.000015,
    input_cost_per_token: float | None = 0.000003,
) -> ModelsConfig:
    return ModelsConfig(
        models={
            "parser": ModelConfig(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                input_cost_per_token_usd=input_cost_per_token,
                output_cost_per_token_usd=output_cost_per_token,
            )
        },
        routing={"skill_draft": RoutingEntry(model="parser")},
        cost=CostConfig(daily_pause_threshold_usd=20.0, monthly_budget_usd=100.0),
        ollama=OllamaConfig(),
        quality_monitoring=QualityMonitoringConfig(),
    )


def _make_task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "skill_draft": TaskTypeEntry(
                description="test",
                model="parser",
                prompt_template="prompts/skill_draft.md",
                output_schema="schemas/skill_draft.json",
            )
        }
    )


def _make_completion_metadata(token_limited: bool = False) -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=100,
        tokens_in=50,
        tokens_out=20,
        cost_usd=0.001,
        model_actual="anthropic/claude-sonnet-4-20250514",
        token_limited=token_limited,
    )


def _make_api_extended_outcome(
    extension_amount_usd: float = 2.50,
    escalation_request_id: int = 42,
    correlation_id: str = "corr-abc",
) -> GateOutcome:
    return GateOutcome(
        fired=True,
        mode="api_extended",
        resolved_by="user",
        escalation_request_id=escalation_request_id,
        correlation_id=correlation_id,
        extension_amount_usd=extension_amount_usd,
    )


def _proceed_outcome() -> GateOutcome:
    """A gate outcome meaning 'budget OK, caller proceeds' (e.g. shadow)."""
    return GateOutcome(
        fired=False,
        mode=None,
        resolved_by=None,
        escalation_request_id=None,
        correlation_id=None,
    )


# ---------------------------------------------------------------------------
# #1 — router-side deterministic estimation so the gate is never dark
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_estimate_cost_floor_uses_alias_rates() -> None:
    """Floor = input_tokens·input_rate + estimate_output_tokens·output_rate."""
    models_config = _make_models_config(
        input_cost_per_token=0.000003, output_cost_per_token=0.000015
    )
    router = ModelRouter(
        models_config=models_config,
        task_types_config=_make_task_types_config(),
        project_root=Path("/nonexistent"),
    )
    prompt = "estimate me please"
    floor = router._estimate_cost_floor("skill_draft", prompt)
    expected = (
        estimate_tokens(prompt) * 0.000003
        + models_config.cost.estimate_output_tokens * 0.000015
    )
    assert floor == pytest.approx(expected)


@pytest.mark.asyncio
async def test_estimate_cost_floor_zero_for_unresolvable_task_type() -> None:
    router = ModelRouter(
        models_config=_make_models_config(),
        task_types_config=_make_task_types_config(),
        project_root=Path("/nonexistent"),
    )
    assert router._estimate_cost_floor("unknown_task_type", "hi") == 0.0


@pytest.mark.asyncio
async def test_gate_consulted_without_caller_estimate() -> None:
    """The gate fires on EVERY call — no reliance on callers passing estimate_usd.

    Regression for the verified S1 defect where no production caller supplied
    estimate_usd, leaving the gate dark.
    """
    models_config = _make_models_config()
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=({"ok": True}, _make_completion_metadata(token_limited=False))
    )

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(return_value=_proceed_outcome())

    budget_guard = MagicMock()
    budget_guard.check_pre_call = AsyncMock()

    router = ModelRouter(
        models_config=models_config,
        task_types_config=_make_task_types_config(),
        project_root=Path("/nonexistent"),
        budget_guard=budget_guard,
        escalation_gate=gate,
    )
    router._providers["anthropic"] = mock_provider

    # NOTE: no estimate_usd passed.
    await router.complete(prompt="do work", task_type="skill_draft", user_id="nick")

    gate.fire_and_wait.assert_awaited_once()
    kwargs = gate.fire_and_wait.call_args.kwargs
    assert kwargs["estimate_source"] == "router_floor"
    expected_floor = router._estimate_cost_floor("skill_draft", "do work")
    assert kwargs["estimate_usd"] == pytest.approx(expected_floor)


# ---------------------------------------------------------------------------
# #3 — spend is logged BEFORE the token-limit raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_logged_before_token_limit_raise() -> None:
    """A token-limited extension call must record its (billed) spend, then raise.

    Regression for the verified S1 defect where the raise preceded the
    invocation_log write, dropping real spend from budget accounting.
    """
    models_config = _make_models_config()

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=({"x": 1}, _make_completion_metadata(token_limited=True))
    )

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(return_value=_make_api_extended_outcome())

    budget_guard = MagicMock()
    budget_guard.check_pre_call = AsyncMock()

    invocation_logger = MagicMock()
    invocation_logger.log = AsyncMock(return_value="inv-1")

    router = ModelRouter(
        models_config=models_config,
        task_types_config=_make_task_types_config(),
        project_root=Path("/nonexistent"),
        budget_guard=budget_guard,
        escalation_gate=gate,
        invocation_logger=invocation_logger,
    )
    router._providers["anthropic"] = mock_provider

    with pytest.raises(TokenLimitReachedError):
        await router.complete(
            prompt="expensive",
            task_type="skill_draft",
            user_id="nick",
            estimate_usd=2.50,
        )

    # Spend was recorded before the raise.
    invocation_logger.log.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_limit_reached_error_raised_when_truncated() -> None:
    """When provider returns token_limited=True, TokenLimitReachedError is raised."""
    models_config = _make_models_config()
    task_types_config = _make_task_types_config()

    mock_provider = MagicMock()
    # Provider returns a token_limited response
    mock_provider.complete = AsyncMock(
        return_value=({"result": "truncated"}, _make_completion_metadata(token_limited=True))
    )

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(return_value=_make_api_extended_outcome())

    budget_guard = MagicMock()
    budget_guard.check_pre_call = AsyncMock()

    router = ModelRouter(
        models_config=models_config,
        task_types_config=task_types_config,
        project_root=Path("/nonexistent"),
        budget_guard=budget_guard,
        escalation_gate=gate,
    )
    # Inject the mock provider directly
    router._providers["anthropic"] = mock_provider

    with pytest.raises(TokenLimitReachedError) as exc_info:
        await router.complete(
            prompt="do something expensive",
            task_type="skill_draft",
            task_id="task-1",
            user_id="nick",
            estimate_usd=2.50,
        )

    err = exc_info.value
    assert err.escalation_request_id == 42
    assert err.correlation_id == "corr-abc"


@pytest.mark.asyncio
async def test_max_tokens_passed_to_provider_for_api_extended() -> None:
    """extension_amount_usd / output_cost_per_token_usd → max_tokens cap."""
    # $2.50 / $0.000015 per token = 166666 tokens
    models_config = _make_models_config(output_cost_per_token=0.000015)
    task_types_config = _make_task_types_config()

    captured_kwargs: dict[str, object] = {}

    async def fake_complete(
        prompt: str, model: str, **kwargs: object
    ) -> tuple[dict[str, bool], CompletionMetadata]:
        captured_kwargs.update(kwargs)
        return {"ok": True}, _make_completion_metadata(token_limited=False)

    mock_provider = MagicMock()
    mock_provider.complete = fake_complete

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(
        return_value=_make_api_extended_outcome(extension_amount_usd=2.50)
    )

    budget_guard = MagicMock()
    budget_guard.check_pre_call = AsyncMock()

    router = ModelRouter(
        models_config=models_config,
        task_types_config=task_types_config,
        project_root=Path("/nonexistent"),
        budget_guard=budget_guard,
        escalation_gate=gate,
    )
    router._providers["anthropic"] = mock_provider

    await router.complete(
        prompt="test",
        task_type="skill_draft",
        user_id="nick",
        estimate_usd=2.50,
    )

    # Input cost is subtracted from extension before computing max_tokens:
    # $2.50 - (1 input token × $0.000003) ≈ $2.499997, then / $0.000015 ≈ 166666.
    assert "max_tokens" in captured_kwargs
    expected = max(1, int((2.50 - (1 * 0.000003)) / 0.000015))
    assert captured_kwargs["max_tokens"] == expected


@pytest.mark.asyncio
async def test_token_limit_reached_when_input_alone_exhausts_budget() -> None:
    """A long prompt whose input cost alone exceeds the extension raises early.

    Without this guard, the extension would be silently overspent on input
    tokens before any output is generated. §10.6 row 1.
    """
    # Extension of $0.001 vs Sonnet input @ $3/M means budget covers ~333 input
    # tokens. A prompt comfortably above that should trip the guard.
    models_config = _make_models_config()
    task_types_config = _make_task_types_config()

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=({"ok": True}, _make_completion_metadata(token_limited=False))
    )

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(
        return_value=_make_api_extended_outcome(extension_amount_usd=0.001)
    )

    budget_guard = MagicMock()
    budget_guard.check_pre_call = AsyncMock()

    router = ModelRouter(
        models_config=models_config,
        task_types_config=task_types_config,
        project_root=Path("/nonexistent"),
        budget_guard=budget_guard,
        escalation_gate=gate,
    )
    router._providers["anthropic"] = mock_provider

    long_prompt = "word " * 5000  # well over the input budget

    with pytest.raises(TokenLimitReachedError):
        await router.complete(
            prompt=long_prompt,
            task_type="skill_draft",
            user_id="nick",
            estimate_usd=0.001,
        )

    # Provider must NOT have been called — guard fires before dispatch.
    mock_provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_no_token_limit_when_not_api_extended() -> None:
    """Normal (non-escalated) calls must NOT pass max_tokens to the provider."""
    models_config = _make_models_config()
    task_types_config = _make_task_types_config()

    captured_kwargs: dict[str, object] = {}

    async def fake_complete(
        prompt: str, model: str, **kwargs: object
    ) -> tuple[dict[str, bool], CompletionMetadata]:
        captured_kwargs.update(kwargs)
        return {"ok": True}, _make_completion_metadata(token_limited=False)

    mock_provider = MagicMock()
    mock_provider.complete = fake_complete

    # Gate not wired — no escalation outcome
    router = ModelRouter(
        models_config=models_config,
        task_types_config=task_types_config,
        project_root=Path("/nonexistent"),
        budget_guard=None,
        escalation_gate=None,
    )
    router._providers["anthropic"] = mock_provider

    await router.complete(prompt="test", task_type="skill_draft", user_id="nick")

    assert "max_tokens" not in captured_kwargs


@pytest.mark.asyncio
async def test_token_limit_not_raised_when_not_escalated() -> None:
    """token_limited=True in a non-escalated call must NOT raise TokenLimitReachedError."""
    models_config = _make_models_config()
    task_types_config = _make_task_types_config()

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=({"ok": True}, _make_completion_metadata(token_limited=True))
    )

    router = ModelRouter(
        models_config=models_config,
        task_types_config=task_types_config,
        project_root=Path("/nonexistent"),
        budget_guard=None,
        escalation_gate=None,
    )
    router._providers["anthropic"] = mock_provider

    # No error: token_limited but no escalation context
    result, _ = await router.complete(
        prompt="test", task_type="skill_draft", user_id="nick"
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_no_max_tokens_when_output_cost_missing() -> None:
    """When output_cost_per_token_usd is None, no max_tokens is set but no error raised."""
    models_config = _make_models_config(output_cost_per_token=None)
    task_types_config = _make_task_types_config()

    captured_kwargs: dict[str, object] = {}

    async def fake_complete(
        prompt: str, model: str, **kwargs: object
    ) -> tuple[dict[str, bool], CompletionMetadata]:
        captured_kwargs.update(kwargs)
        return {"ok": True}, _make_completion_metadata(token_limited=False)

    mock_provider = MagicMock()
    mock_provider.complete = fake_complete

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(
        return_value=_make_api_extended_outcome(extension_amount_usd=2.50)
    )

    budget_guard = MagicMock()
    budget_guard.check_pre_call = AsyncMock()

    router = ModelRouter(
        models_config=models_config,
        task_types_config=task_types_config,
        project_root=Path("/nonexistent"),
        budget_guard=budget_guard,
        escalation_gate=gate,
    )
    router._providers["anthropic"] = mock_provider

    result, _ = await router.complete(
        prompt="test", task_type="skill_draft", user_id="nick", estimate_usd=2.50
    )

    assert "max_tokens" not in captured_kwargs
    assert result == {"ok": True}
