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

    # Proactive prompts (2026-07-02 wiring): must start when set.
    evening = MagicMock()
    evening.run = _stub_runner("evening_checkin", started)
    afternoon = MagicMock()
    afternoon.run = _stub_runner("afternoon_inactivity", started)
    stale = MagicMock()
    stale.run = _stub_runner("stale_detector", started)
    post_meeting = MagicMock()
    post_meeting.run = _stub_runner("post_meeting_capture", started)

    nt = NotificationTasks(
        reminder_scheduler=reminder,
        overdue_detector=overdue,
        morning_digest=digest,
        weekly_planner=weekly,
        evening_checkin=evening,
        afternoon_inactivity=afternoon,
        stale_detector=stale,
        post_meeting_capture=post_meeting,
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
    assert "evening_checkin" in started
    assert "afternoon_inactivity" in started
    assert "stale_detector" in started
    assert "post_meeting_capture" in started


@pytest.mark.asyncio
async def test_run_server_surfaces_crashed_bg_task() -> None:
    """A background loop that crashes is surfaced (Discord alert), not silent."""

    async def _boom() -> None:
        raise RuntimeError("kaboom")

    reminder = MagicMock()
    reminder.run = AsyncMock(side_effect=_boom)
    overdue = MagicMock()
    overdue.run = _stub_runner("overdue_detector", [])
    digest = MagicMock()
    digest.run = _stub_runner("morning_digest", [])

    nt = NotificationTasks(
        reminder_scheduler=reminder,
        overdue_detector=overdue,
        morning_digest=digest,
    )

    discord_bot = MagicMock()
    discord_bot.send_message = AsyncMock()
    discord_bot.is_ready = MagicMock(return_value=True)

    server_task = asyncio.create_task(
        run_server(
            host="127.0.0.1",
            port=0,
            notification_tasks=nt,
            discord_bot=discord_bot,
        )
    )

    await asyncio.sleep(0.2)
    server_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await server_task

    # The crashed reminder loop triggered a Discord debug alert.
    assert discord_bot.send_message.called
    alert_text = discord_bot.send_message.call_args[0][1]
    assert "crashed" in alert_text
    assert "reminder_scheduler" in alert_text
