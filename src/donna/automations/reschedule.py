"""One-shot realignment of automation next_run_at after a cron-tz change.

Idempotent: recomputes next_run_at for every active on_schedule automation
using the supplied (tz-aware) cron calculator. Safe to run on every startup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()


async def recompute_next_runs(repo: Any, cron: Any, now: datetime) -> int:
    """Recompute next_run_at for all active on_schedule automations.

    Args:
        repo: AutomationRepository (needs list_all + update_fields).
        cron: CronScheduleCalculator configured with the user timezone.
        now: Reference time for the next-run computation (UTC).

    Returns:
        Number of automations whose schedule was recomputed.
    """
    automations = await repo.list_all(status="active", limit=1000)
    count = 0
    for automation in automations:
        if automation.trigger_type != "on_schedule" or not automation.schedule:
            continue
        try:
            next_run_at = cron.next_run(expression=automation.schedule, after=now)
        except Exception as exc:
            logger.warning(
                "automation_reschedule_invalid_cron",
                automation_id=automation.id,
                schedule=automation.schedule,
                error=str(exc),
            )
            continue
        await repo.update_fields(automation.id, next_run_at=next_run_at)
        count += 1
    if count:
        logger.info("automation_next_runs_recomputed", count=count)
    return count
