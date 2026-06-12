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
from typing import TYPE_CHECKING

import structlog

from donna.config import ModelsConfig
from donna.cost.tracker import CostTracker

if TYPE_CHECKING:
    from donna.cost.budget_extension import BudgetExtensionRepository

logger = structlog.get_logger()

# Notifier type: async callable(channel_name, message) → None
Notifier = Callable[[str, str], Awaitable[None]]


class BudgetPausedError(Exception):
    """Raised when spend has hit a budget pause threshold.

    All autonomous LLM work should stop — until the next UTC day for a
    ``daily`` pause, or until the next month / an approved budget increase
    for a ``monthly`` pause. The ``daily_spent`` / ``daily_limit`` attribute
    names are retained for backwards compatibility; for a monthly pause they
    carry the monthly figures and ``period == "monthly"``.
    """

    def __init__(
        self, daily_spent: float, daily_limit: float, period: str = "daily"
    ) -> None:
        self.daily_spent = daily_spent
        self.daily_limit = daily_limit
        self.period = period
        super().__init__(
            f"{period.capitalize()} budget hit: ${daily_spent:.4f} ≥ "
            f"${daily_limit:.2f}. Autonomous work paused."
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
        extension_repo: BudgetExtensionRepository | None = None,
    ) -> None:
        self._tracker = tracker
        self._cost_config = models_config.cost
        self._notifier = notifier
        self._extension_repo = extension_repo
        # Track which months we've already sent a warning for (in-memory).
        self._warned_months: set[tuple[int, int]] = set()

    async def check_pre_call(self, user_id: str = "system") -> None:
        """Raise BudgetPausedError if daily spend exceeds the pause threshold.

        Call this before every LLM API call. If the threshold is reached,
        a Discord notification is sent (if notifier is configured) and
        BudgetPausedError is raised.
        """
        # Escalation audit rows (slice 17) and tool-gap audit rows
        # (slice 22) carry zero spend by construction; excluding them is
        # defensive against schema drift.
        daily_summary = await self._tracker.get_daily_cost(
            exclude_task_types=[
                "external_llm_call",
                "escalation_lifecycle",
                "tool_gap_lifecycle",
            ]
        )
        spent = daily_summary.total_usd
        limit = self._cost_config.daily_pause_threshold_usd

        # Factor in any approved extensions to raise the effective cap.
        if self._extension_repo is not None:
            try:
                extension_total = await self._extension_repo.get_daily_total(
                    user_id, date.today()
                )
                limit = limit + extension_total
            except Exception:
                logger.exception("budget_guard_extension_lookup_failed", user_id=user_id)

        if spent >= limit:
            msg = (
                f"Daily budget hit: ${spent:.2f} spent (limit ${limit:.2f}). "
                "I'm pausing autonomous work for the rest of the day. "
                "Come back tomorrow — or raise the limit if you need me now."
            )
            logger.warning(
                "budget_daily_threshold_hit",
                daily_spent=spent,
                effective_daily_limit=limit,
                user_id=user_id,
            )
            if self._notifier is not None:
                try:
                    await self._notifier("debug", msg)
                except Exception:
                    logger.exception("budget_notifier_failed")
            raise BudgetPausedError(daily_spent=spent, daily_limit=limit)

        # Monthly hard cap (spec_v3.md §13.1 / §18.3). Approved daily
        # extensions let spend exceed $20/day but still accumulate toward the
        # $100 monthly cap; there is no separate "monthly budget increase"
        # mechanism yet, so the configured budget IS the cap. Enforced
        # regardless of the escalation-gate posture (shadow or enforce).
        monthly_summary = await self._tracker.get_monthly_cost(
            exclude_task_types=[
                "external_llm_call",
                "escalation_lifecycle",
                "tool_gap_lifecycle",
            ]
        )
        monthly_spent = monthly_summary.total_usd
        monthly_cap = self._cost_config.monthly_budget_usd
        if monthly_spent >= monthly_cap:
            msg = (
                f"Monthly budget hit: ${monthly_spent:.2f} of ${monthly_cap:.2f} "
                "used. I'm pausing autonomous work for the rest of the month. "
                "Approve a budget increase if you need me to keep going."
            )
            logger.warning(
                "budget_monthly_threshold_hit",
                monthly_spent=monthly_spent,
                monthly_cap=monthly_cap,
                user_id=user_id,
            )
            if self._notifier is not None:
                try:
                    await self._notifier("debug", msg)
                except Exception:
                    logger.exception("budget_monthly_notifier_failed")
            raise BudgetPausedError(
                daily_spent=monthly_spent,
                daily_limit=monthly_cap,
                period="monthly",
            )

        # Below the hard cap — fire the one-shot 90% warning if applicable.
        await self.check_monthly_warning(user_id)

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
