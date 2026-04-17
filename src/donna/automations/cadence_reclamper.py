"""CadenceReclamper — recomputes automation.active_cadence_cron on skill state change.

Registered via SkillLifecycleManager.after_state_change. When a skill transitions
(e.g. sandbox -> shadow_primary), all automations pointing at that capability
get their active_cadence_cron recomputed per the cadence policy.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.automations.cadence_policy import CadencePolicy, PausedState

logger = structlog.get_logger()


class CadenceReclamper:
    """Recomputes automation.active_cadence_cron on skill state change."""

    def __init__(self, *, repo: Any, policy: CadencePolicy, scheduler: Any) -> None:
        self._repo = repo
        self._policy = policy
        self._scheduler = scheduler

    async def reclamp_for_capability(
        self, capability_name: str, new_state: str
    ) -> int:
        """Recompute active cadence for every automation pointing at *capability_name*.

        Returns the number of automations whose active cadence actually changed.
        """
        rows = await self._repo.list_by_capability(capability_name)
        changed = 0
        for row in rows:
            try:
                new_active: str | None = self._compute_active(
                    row.target_cadence_cron, new_state
                )
            except PausedState:
                new_active = None
            if new_active == row.active_cadence_cron:
                continue
            next_run_at = None
            if new_active is not None and hasattr(self._scheduler, "compute_next_run"):
                next_run_at = await self._scheduler.compute_next_run(new_active)
            await self._repo.update_active_cadence(row.id, new_active, next_run_at)
            logger.info(
                "cadence_reclamped",
                automation_id=row.id,
                capability=capability_name,
                new_state=new_state,
                old_active=row.active_cadence_cron,
                new_active=new_active,
                target=row.target_cadence_cron,
            )
            changed += 1
        if changed > 50:
            logger.warning(
                "cadence_reclamp_large_batch",
                count=changed,
                capability=capability_name,
            )
        return changed

    def _compute_active(self, target_cron: str | None, lifecycle_state: str) -> str:
        """Compute the active cadence for *target_cron* given *lifecycle_state*.

        Raises PausedState if the lifecycle state is paused (caller maps to None).
        """
        min_interval = self._policy.min_interval_for(lifecycle_state)
        if target_cron is None:
            return _seconds_to_cron(min_interval)
        target_interval = _cron_min_interval_seconds(target_cron)
        if target_interval >= min_interval:
            return target_cron
        return _seconds_to_cron(min_interval)


def _cron_min_interval_seconds(cron: str) -> int:
    """Best-effort interval estimation from a cron string.

    Recognizes: ``*/N * * * *`` (every N minutes), ``0 */N * * *`` (every N
    hours), ``0 N * * *`` (daily at hour N), and ``0 N * * W`` (weekly).
    Falls back to daily (86400) for anything else.
    """
    parts = cron.split()
    if len(parts) < 5:
        return 86400
    minute, hour, dom, month, dow = parts[:5]
    if minute.startswith("*/"):
        try:
            return int(minute[2:]) * 60
        except ValueError:
            return 86400
    if hour.startswith("*/"):
        try:
            return int(hour[2:]) * 3600
        except ValueError:
            return 86400
    if hour == "*" and minute == "0":
        return 3600  # hourly
    if dow.isdigit() and dow != "*":
        return 7 * 86400  # weekly
    if dom == "*" and month == "*" and dow == "*":
        return 86400  # daily
    return 86400


def _seconds_to_cron(seconds: int) -> str:
    """Map a minimum interval in seconds to a representative cron expression."""
    if seconds <= 900:
        mins = max(1, seconds // 60)
        return f"*/{mins} * * * *"
    if seconds <= 3600:
        return "0 * * * *"
    if seconds <= 43200:
        return "0 */12 * * *"
    return "0 0 * * *"
