import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from donna.skills.crons.scheduler import AsyncCronScheduler, _next_fire


async def test_scheduler_run_once_fires_task():
    task = AsyncMock()
    scheduler = AsyncCronScheduler(
        hour_utc=0, task=task,
        now_fn=lambda: datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
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
        now_fn=lambda: datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
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
        now_fn=lambda: datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    scheduler.stop()
    await asyncio.wait_for(scheduler.run_forever(), timeout=0.5)
    task.assert_not_awaited()


# Slice 16 — weekly + minute_utc extensions ---------------------------


def test_next_fire_daily_same_day_future() -> None:
    """Daily fire later today → returns today's time."""
    now = datetime(2026, 4, 24, 18, 30, tzinfo=UTC)  # Friday
    nxt = _next_fire(now, hour_utc=21, minute_utc=0)
    assert nxt == datetime(2026, 4, 24, 21, 0, tzinfo=UTC)


def test_next_fire_daily_same_day_past() -> None:
    """Daily fire time already passed today → tomorrow."""
    now = datetime(2026, 4, 24, 22, 15, tzinfo=UTC)
    nxt = _next_fire(now, hour_utc=21, minute_utc=0)
    assert nxt == datetime(2026, 4, 25, 21, 0, tzinfo=UTC)


def test_next_fire_minute_granularity() -> None:
    """``minute_utc`` is respected."""
    now = datetime(2026, 4, 24, 20, 15, tzinfo=UTC)
    nxt = _next_fire(now, hour_utc=20, minute_utc=30)
    assert nxt == datetime(2026, 4, 24, 20, 30, tzinfo=UTC)


def test_next_fire_weekly_future_this_week() -> None:
    """Weekly Sunday 21:00, now is Friday → this coming Sunday."""
    now = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)  # Friday
    nxt = _next_fire(now, hour_utc=21, minute_utc=0, day_of_week=6)
    assert nxt == datetime(2026, 4, 26, 21, 0, tzinfo=UTC)


def test_next_fire_weekly_today_but_past_hour() -> None:
    """Weekly Sunday 21:00, now is Sunday 22:00 → next Sunday."""
    now = datetime(2026, 4, 26, 22, 0, tzinfo=UTC)  # Sunday
    nxt = _next_fire(now, hour_utc=21, minute_utc=0, day_of_week=6)
    assert nxt == datetime(2026, 5, 3, 21, 0, tzinfo=UTC)


def test_next_fire_weekly_today_before_hour() -> None:
    """Weekly Sunday 21:00, now is Sunday 14:00 → today 21:00."""
    now = datetime(2026, 4, 26, 14, 0, tzinfo=UTC)  # Sunday
    nxt = _next_fire(now, hour_utc=21, minute_utc=0, day_of_week=6)
    assert nxt == datetime(2026, 4, 26, 21, 0, tzinfo=UTC)


def test_scheduler_rejects_invalid_args() -> None:
    task = AsyncMock()
    with pytest.raises(ValueError):
        AsyncCronScheduler(hour_utc=25, task=task)
    with pytest.raises(ValueError):
        AsyncCronScheduler(hour_utc=0, task=task, minute_utc=60)
    with pytest.raises(ValueError):
        AsyncCronScheduler(hour_utc=0, task=task, day_of_week=7)
