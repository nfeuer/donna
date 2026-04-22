"""AsyncCronScheduler — fires a provided async task once per UTC day at a given hour.

Designed to run as asyncio.create_task inside the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog

logger = structlog.get_logger()


class AsyncCronScheduler:
    def __init__(
        self,
        hour_utc: int,
        task: Callable[[], Awaitable[None]],
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._hour = hour_utc
        self._task = task
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run_once(self) -> None:
        """Execute the task once (used for tests and manual triggers)."""
        try:
            await self._task()
        except Exception:
            logger.exception("cron_task_failed", hour_utc=self._hour)

    async def run_forever(self) -> None:
        """Sleep until the next fire time, execute, repeat until stop()."""
        while not self._stop:
            now = self._now_fn()
            next_fire = _next_fire(now, self._hour)
            wait_seconds = max(0.0, (next_fire - now).total_seconds())
            logger.info(
                "cron_scheduled_next_fire",
                next_fire=next_fire.isoformat(),
                wait_s=int(wait_seconds),
            )
            await self._sleep_fn(wait_seconds)
            if self._stop:
                return
            await self.run_once()


def _next_fire(now: datetime, hour_utc: int) -> datetime:
    today = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if today > now:
        return today
    return today + timedelta(days=1)
