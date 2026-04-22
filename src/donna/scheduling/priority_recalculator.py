"""Daily priority recalculator background task — Phase 2.

Wraps PriorityEngine in a background loop that fires daily at 6:00 AM
(configurable). Loads all non-done tasks, computes priority changes, persists
them via Database.update_task(), and logs each change.

See docs/scheduling.md.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from donna.notifications.service import NotificationService
from donna.scheduling.priority_engine import PriorityEngine
from donna.tasks.database import Database

logger = structlog.get_logger()


class PriorityRecalculator:
    """Background loop that re-evaluates task priorities daily.

    Usage:
        recalculator = PriorityRecalculator(db, engine, service, user_id)
        asyncio.create_task(recalculator.run())
    """

    def __init__(
        self,
        db: Database,
        engine: PriorityEngine,
        service: NotificationService,
        user_id: str,
        fire_hour: int = 6,
        fire_minute: int = 0,
    ) -> None:
        self._db = db
        self._engine = engine
        self._service = service
        self._user_id = user_id
        self._fire_hour = fire_hour
        self._fire_minute = fire_minute

    async def run(self) -> None:
        """Sleep until the next fire time, recalculate, repeat."""
        logger.info(
            "priority_recalculator_started",
            fire_hour=self._fire_hour,
            fire_minute=self._fire_minute,
            user_id=self._user_id,
        )

        while True:
            now = datetime.now(tz=UTC)
            next_fire = _next_fire_time(now, self._fire_hour, self._fire_minute)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "priority_recalculator_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self.recalculate_and_apply(datetime.now(tz=UTC))
            except Exception:
                logger.exception("priority_recalculator_failed", user_id=self._user_id)

    async def recalculate_and_apply(self, now: datetime) -> list[tuple[str, int, int]]:
        """Load tasks, compute changes, persist. Returns list of changes."""
        tasks = await self._db.list_tasks(user_id=self._user_id)
        changes = self._engine.recalculate(tasks, now)

        for task_id, old_priority, new_priority in changes:
            await self._db.update_task(task_id, priority=new_priority)
            logger.info(
                "priority_updated",
                task_id=task_id,
                old_priority=old_priority,
                new_priority=new_priority,
                user_id=self._user_id,
            )

        if changes:
            summary = ", ".join(
                f"task {tid}: {old}→{new}" for tid, old, new in changes
            )
            logger.info("priority_recalculation_summary", changes=summary, user_id=self._user_id)

        return changes


def _next_fire_time(now: datetime, hour: int, minute: int) -> datetime:
    """Return the next datetime at hour:minute (UTC), at least 1 second away."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
