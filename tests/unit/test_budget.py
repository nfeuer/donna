"""Tests for BudgetGuard — pre-call spend checks and threshold notifications."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import CostConfig, ModelConfig, ModelsConfig, RoutingEntry
from donna.cost.budget import BudgetGuard, BudgetPausedError
from donna.cost.tracker import CostSummary, CostTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_models_config(
    daily_pause: float = 20.0,
    monthly_budget: float = 100.0,
    monthly_warning_pct: float = 0.90,
) -> ModelsConfig:
    return ModelsConfig(
        models={"parser": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514")},
        routing={"parse_task": RoutingEntry(model="parser")},
        cost=CostConfig(
            monthly_budget_usd=monthly_budget,
            daily_pause_threshold_usd=daily_pause,
            monthly_warning_pct=monthly_warning_pct,
        ),
    )


def _make_tracker(daily_total: float, monthly_total: float = 0.0) -> CostTracker:
    tracker = MagicMock(spec=CostTracker)
    tracker.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_total, call_count=5, breakdown={})
    )
    tracker.get_monthly_cost = AsyncMock(
        return_value=CostSummary(
            total_usd=monthly_total,
            call_count=20,
            breakdown={"parse_task": monthly_total},
        ),
    )
    return tracker


# ---------------------------------------------------------------------------
# check_pre_call
# ---------------------------------------------------------------------------


class TestCheckPreCall:
    async def test_below_threshold_no_error(self) -> None:
        """$15 daily spend, $20 limit → no error."""
        tracker = _make_tracker(daily_total=15.00)
        guard = BudgetGuard(tracker, _make_models_config(daily_pause=20.0))
        await guard.check_pre_call("nick")  # should not raise

    async def test_exactly_at_threshold_raises(self) -> None:
        """$20.00 daily spend, $20.00 limit → raises."""
        tracker = _make_tracker(daily_total=20.00)
        guard = BudgetGuard(tracker, _make_models_config(daily_pause=20.0))
        with pytest.raises(BudgetPausedError) as exc_info:
            await guard.check_pre_call("nick")
        assert exc_info.value.daily_spent == 20.00
        assert exc_info.value.daily_limit == 20.00

    async def test_above_threshold_raises(self) -> None:
        """$20.01 daily spend → BudgetPausedError."""
        tracker = _make_tracker(daily_total=20.01)
        guard = BudgetGuard(tracker, _make_models_config(daily_pause=20.0))
        with pytest.raises(BudgetPausedError):
            await guard.check_pre_call("nick")

    async def test_notifier_called_on_threshold(self) -> None:
        """When threshold is hit, notifier is called with 'debug' channel."""
        notifier = AsyncMock()
        tracker = _make_tracker(daily_total=25.00)
        guard = BudgetGuard(tracker, _make_models_config(daily_pause=20.0), notifier=notifier)

        with pytest.raises(BudgetPausedError):
            await guard.check_pre_call("nick")

        notifier.assert_called_once()
        channel, message = notifier.call_args[0]
        assert channel == "debug"
        assert "20.00" in message or "20" in message  # limit mentioned

    async def test_no_notifier_still_raises(self) -> None:
        """BudgetPausedError raised even without notifier configured."""
        tracker = _make_tracker(daily_total=50.00)
        guard = BudgetGuard(tracker, _make_models_config(daily_pause=20.0), notifier=None)
        with pytest.raises(BudgetPausedError):
            await guard.check_pre_call("nick")

    async def test_zero_spend_no_error(self) -> None:
        """Zero spend → no error."""
        tracker = _make_tracker(daily_total=0.0)
        guard = BudgetGuard(tracker, _make_models_config())
        await guard.check_pre_call("nick")


# ---------------------------------------------------------------------------
# check_monthly_warning
# ---------------------------------------------------------------------------


class TestCheckMonthlyWarning:
    async def test_at_90pct_sends_warning(self) -> None:
        """$91 of $100 budget (91%) → warning sent, returns True."""
        notifier = AsyncMock()
        tracker = _make_tracker(daily_total=3.0, monthly_total=91.0)
        guard = BudgetGuard(
            tracker,
            _make_models_config(monthly_budget=100.0, monthly_warning_pct=0.90),
            notifier=notifier,
        )

        result = await guard.check_monthly_warning("nick")

        assert result is True
        notifier.assert_called_once()
        channel, _message = notifier.call_args[0]
        assert channel == "debug"

    async def test_below_threshold_no_warning(self) -> None:
        """$80 of $100 (80%) → no warning."""
        notifier = AsyncMock()
        tracker = _make_tracker(daily_total=3.0, monthly_total=80.0)
        guard = BudgetGuard(
            tracker,
            _make_models_config(monthly_budget=100.0, monthly_warning_pct=0.90),
            notifier=notifier,
        )

        result = await guard.check_monthly_warning("nick")

        assert result is False
        notifier.assert_not_called()

    async def test_warning_sent_only_once_per_month(self) -> None:
        """Warning not resent if already warned this month."""
        notifier = AsyncMock()
        tracker = _make_tracker(daily_total=3.0, monthly_total=95.0)
        guard = BudgetGuard(tracker, _make_models_config(), notifier=notifier)

        first = await guard.check_monthly_warning("nick")
        second = await guard.check_monthly_warning("nick")

        assert first is True
        assert second is False
        assert notifier.call_count == 1

    async def test_no_notifier_still_returns_true(self) -> None:
        """Returns True even if notifier is None."""
        tracker = _make_tracker(daily_total=3.0, monthly_total=95.0)
        guard = BudgetGuard(tracker, _make_models_config(), notifier=None)
        result = await guard.check_monthly_warning("nick")
        assert result is True


# ---------------------------------------------------------------------------
# BudgetPausedError attributes
# ---------------------------------------------------------------------------


class TestBudgetPausedError:
    def test_attributes_set_correctly(self) -> None:
        err = BudgetPausedError(daily_spent=22.50, daily_limit=20.0)
        assert err.daily_spent == 22.50
        assert err.daily_limit == 20.0
        assert "22.50" in str(err) or "22" in str(err)
