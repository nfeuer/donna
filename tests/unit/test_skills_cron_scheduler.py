import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from donna.skills.crons.scheduler import AsyncCronScheduler


async def test_scheduler_run_once_fires_task():
    task = AsyncMock()
    scheduler = AsyncCronScheduler(
        hour_utc=0, task=task,
        now_fn=lambda: datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    task.assert_awaited_once()


async def test_scheduler_does_not_fire_before_hour():
    """When `now` is 02:00 and hour is 03:00, sleep is scheduled for 3600s.
    Before sleep returns we cancel via the sleep stub."""
    task = AsyncMock()
    raised = False

    async def _sleep(secs):
        nonlocal raised
        if not raised:
            raised = True
            raise asyncio.CancelledError()

    scheduler = AsyncCronScheduler(
        hour_utc=3, task=task,
        now_fn=lambda: datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc),
        sleep_fn=_sleep,
    )
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever()
    task.assert_not_awaited()


async def test_scheduler_stop_before_loop_exits_cleanly():
    """If stop() is called before run_forever starts, the loop exits
    immediately without invoking the task."""
    task = AsyncMock()
    scheduler = AsyncCronScheduler(
        hour_utc=0, task=task,
        now_fn=lambda: datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    scheduler.stop()
    await asyncio.wait_for(scheduler.run_forever(), timeout=0.5)
    task.assert_not_awaited()
