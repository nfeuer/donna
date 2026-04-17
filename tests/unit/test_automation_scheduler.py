import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.scheduler import AutomationScheduler


async def test_scheduler_run_once_dispatches_due_automations():
    mock_due_a = MagicMock(id="a1")
    mock_due_b = MagicMock(id="a2")
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[mock_due_a, mock_due_b])

    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    assert dispatcher.dispatch.await_count == 2


async def test_scheduler_run_once_does_nothing_when_no_due():
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    dispatcher.dispatch.assert_not_awaited()


async def test_scheduler_dispatch_errors_do_not_stop_loop():
    mock_due_a = MagicMock(id="a1")
    mock_due_b = MagicMock(id="a2")
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[mock_due_a, mock_due_b])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(side_effect=[RuntimeError("broke"), None])

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    assert dispatcher.dispatch.await_count == 2


async def test_scheduler_stop_signal_exits_loop():
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[])
    dispatcher = MagicMock()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    scheduler.stop()
    await asyncio.wait_for(scheduler.run_forever(), timeout=0.5)


async def test_scheduler_run_forever_polls_until_stopped():
    mock_due = MagicMock(id="a1")
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[mock_due])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    ticks = 0

    async def _sleep(_secs):
        nonlocal ticks
        ticks += 1
        if ticks >= 2:
            scheduler.stop()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=_sleep,
    )
    await asyncio.wait_for(scheduler.run_forever(), timeout=1.0)
    assert dispatcher.dispatch.await_count >= 1
