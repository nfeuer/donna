"""Budget enforcement — pre-call spend checks and threshold notifications.

Checks daily and monthly spend from invocation_log before every LLM call.
On threshold breach: notifies the user via Discord #donna-debug, then
raises BudgetPausedError to stop autonomous agent work.

Thresholds are loaded from config/donna_models.yaml:
  cost.daily_pause_threshold_usd  — $20 default, pauses all LLM calls
  cost.monthly_budget_usd         — $100 default, hard cap reference
  cost.monthly_warning_pct        — 0.90 default, warning at 90%

See CLAUDE.md (Budget section) and docs/model-layer.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

import structlog

from donna.config import ModelsConfig
from donna.cost.tracker import CostTracker

logger = structlog.get_logger()

# Notifier type: async callable(channel_name, message) → None
Notifier = Callable[[str, str], Awaitable[None]]


class BudgetPausedError(Exception):
    """Raised when daily spend has hit the pause threshold.

    All autonomous LLM work should stop until the next UTC day.
    """

    def __init__(self, daily_spent: float, daily_limit: float) -> None:
        self.daily_spent = daily_spent
        self.daily_limit = daily_limit
        super().__init__(
            f"Daily budget hit: ${daily_spent:.4f} ≥ ${daily_limit:.2f}. "
            "Autonomous work paused."
        )


class BudgetGuard:
    """Pre-call budget enforcement.

    Inject into ModelRouter to gate every LLM call against spend limits.
    The optional notifier is called with (channel, message) to send
    Discord alerts; pass DonnaBot.send_message.
    """

    def __init__(
        self,
        tracker: CostTracker,
        models_config: ModelsConfig,
        notifier: Notifier | None = None,
    ) -> None:
        self._tracker = tracker
        self._cost_config = models_config.cost
        self._notifier = notifier
        # Track which months we've already sent a warning for (in-memory).
        self._warned_months: set[tuple[int, int]] = set()

    async def check_pre_call(self, user_id: str = "system") -> None:
        """Raise BudgetPausedError if daily spend exceeds the pause threshold.

        Call this before every LLM API call. If the threshold is reached,
        a Discord notification is sent (if notifier is configured) and
        BudgetPausedError is raised.
        """
        daily_summary = await self._tracker.get_daily_cost(
            exclude_task_types=["external_llm_call"]
        )
        spent = daily_summary.total_usd
        limit = self._cost_config.daily_pause_threshold_usd

        if spent >= limit:
            msg = (
                f"Daily budget hit: ${spent:.2f} spent (limit ${limit:.2f}). "
                "I'm pausing autonomous work for the rest of the day. "
                "Come back tomorrow — or raise the limit if you need me now."
            )
            logger.warning(
                "budget_daily_threshold_hit",
                daily_spent=spent,
                daily_limit=limit,
                user_id=user_id,
            )
            if self._notifier is not None:
                try:
                    await self._notifier("debug", msg)
                except Exception:
                    logger.exception("budget_notifier_failed")
            raise BudgetPausedError(daily_spent=spent, daily_limit=limit)

    async def check_monthly_warning(self, user_id: str = "system") -> bool:
        """Send a monthly budget warning if spend is at or above warning_pct.

        Returns True if a warning was sent, False otherwise.
        Only sends once per calendar month (tracked in memory).
        """
        today = date.today()
        month_key = (today.year, today.month)
        if month_key in self._warned_months:
            return False

        monthly_summary = await self._tracker.get_monthly_cost()
        spent = monthly_summary.total_usd
        budget = self._cost_config.monthly_budget_usd
        threshold = budget * self._cost_config.monthly_warning_pct

        if spent < threshold:
            return False

        breakdown_lines = "\n".join(
            f"  {task_type}: ${cost:.4f}"
            for task_type, cost in sorted(
                monthly_summary.breakdown.items(), key=lambda x: -x[1]
            )
        )
        msg = (
            f"Monthly budget warning: ${spent:.2f} of ${budget:.2f} used "
            f"({spent / budget * 100:.0f}%). "
            f"Breakdown:\n{breakdown_lines}\n"
            "Projected month-end spend may exceed budget."
        )

        logger.warning(
            "budget_monthly_warning",
            monthly_spent=spent,
            monthly_budget=budget,
            warning_pct=self._cost_config.monthly_warning_pct,
            user_id=user_id,
        )

        if self._notifier is not None:
            try:
                await self._notifier("debug", msg)
            except Exception:
                logger.exception("budget_monthly_notifier_failed")

        self._warned_months.add(month_key)
        return True
