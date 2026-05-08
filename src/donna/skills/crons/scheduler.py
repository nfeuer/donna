"""AsyncCronScheduler — fires an async task daily or weekly.

Designed to run as ``asyncio.create_task`` inside the FastAPI lifespan.

When *tz* is provided, *hour_utc* and *minute_utc* are interpreted in
that timezone (despite the parameter names, kept for backwards compat).
Otherwise they are interpreted as UTC.
"""

from __future__ import annotations

import asyncio
import zoneinfo
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
        *,
        minute_utc: int = 0,
        day_of_week: int | None = None,
        tz: zoneinfo.ZoneInfo | None = None,
    ) -> None:
        if not 0 <= hour_utc <= 23:
            raise ValueError(f"hour_utc must be 0..23, got {hour_utc}")
        if not 0 <= minute_utc <= 59:
            raise ValueError(f"minute_utc must be 0..59, got {minute_utc}")
        if day_of_week is not None and not 0 <= day_of_week <= 6:
            raise ValueError(
                f"day_of_week must be 0..6 (Mon..Sun), got {day_of_week}"
            )
        self._hour = hour_utc
        self._minute = minute_utc
        self._day_of_week = day_of_week
        self._task = task
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._stop = False
        self._tz = tz

    def stop(self) -> None:
        self._stop = True

    async def run_once(self) -> None:
        """Execute the task once (used for tests and manual triggers)."""
        try:
            await self._task()
        except Exception:
            logger.exception(
                "cron_task_failed",
                hour_utc=self._hour,
                minute_utc=self._minute,
                day_of_week=self._day_of_week,
            )

    async def run_forever(self) -> None:
        """Sleep until the next fire time, execute, repeat until stop()."""
        while not self._stop:
            now = self._now_fn()
            next_fire = _next_fire(
                now, self._hour, self._minute, self._day_of_week, self._tz
            )
            wait_seconds = max(0.0, (next_fire - now).total_seconds())
            logger.info(
                "cron_scheduled_next_fire",
                next_fire=next_fire.isoformat(),
                wait_s=int(wait_seconds),
                hour_utc=self._hour,
                minute_utc=self._minute,
                day_of_week=self._day_of_week,
            )
            await self._sleep_fn(wait_seconds)
            if self._stop:
                return
            await self.run_once()


def _next_fire(
    now: datetime,
    hour_utc: int,
    minute_utc: int = 0,
    day_of_week: int | None = None,
    tz: zoneinfo.ZoneInfo | None = None,
) -> datetime:
    """Calculate the next fire time.

    When *tz* is provided, *hour_utc* and *minute_utc* are interpreted in
    that timezone and the returned datetime is UTC.
    """
    if tz is not None:
        local_now = now.astimezone(tz)
        candidate = local_now.replace(
            hour=hour_utc, minute=minute_utc, second=0, microsecond=0
        )
    else:
        candidate = now.replace(
            hour=hour_utc, minute=minute_utc, second=0, microsecond=0
        )
        local_now = now

    if day_of_week is None:
        if candidate > local_now:
            return candidate.astimezone(UTC) if tz else candidate
        result = candidate + timedelta(days=1)
        return result.astimezone(UTC) if tz else result

    # Weekly: bump forward to the next occurrence of day_of_week.
    days_ahead = (day_of_week - candidate.weekday()) % 7
    if days_ahead == 0 and candidate <= local_now:
        days_ahead = 7
    result = candidate + timedelta(days=days_ahead)
    return result.astimezone(UTC) if tz else result
