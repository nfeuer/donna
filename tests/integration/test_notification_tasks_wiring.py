"""Integration: NotificationTasks components start via run_server()."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.server import NotificationTasks, run_server


def _stub_runner(name: str, started: list[str]) -> AsyncMock:
    async def _run():
        started.append(name)
        await asyncio.sleep(999)

    mock = AsyncMock(side_effect=_run)
    return mock


@pytest.mark.asyncio
async def test_run_server_starts_notification_tasks() -> None:
    """run_server() creates asyncio tasks for all required NotificationTasks components."""
    started: list[str] = []

    reminder = MagicMock()
    reminder.run = _stub_runner("reminder_scheduler", started)

    overdue = MagicMock()
    overdue.run = _stub_runner("overdue_detector", started)

    digest = MagicMock()
    digest.run = _stub_runner("morning_digest", started)

    weekly = MagicMock()
    weekly.run = _stub_runner("weekly_planner", started)

    nt = NotificationTasks(
        reminder_scheduler=reminder,
        overdue_detector=overdue,
        morning_digest=digest,
        weekly_planner=weekly,
    )

    server_task = asyncio.create_task(
        run_server(
            host="127.0.0.1",
            port=0,
            notification_tasks=nt,
        )
    )

    # Give run_server time to start background tasks
    await asyncio.sleep(0.2)
    server_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await server_task

    assert "reminder_scheduler" in started
    assert "overdue_detector" in started
    assert "morning_digest" in started
    assert "weekly_planner" in started
