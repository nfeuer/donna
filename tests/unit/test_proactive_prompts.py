"""Unit tests for proactive prompt background loops."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.notifications.proactive_prompts import (
    AfternoonInactivityCheck,
    EveningCheckin,
    PostMeetingCapture,
    StaleTaskDetector,
    _next_fire_time,
)


def _make_task_row(**overrides: object) -> MagicMock:
    """Build a minimal TaskRow-like mock."""
    row = MagicMock()
    row.id = "task-abc-123"
    row.title = "Buy milk"
    row.domain = "personal"
    row.priority = 2
    row.status = "backlog"
    row.scheduled_start = None
    row.created_at = "2024-03-01T10:00:00"
    row.completed_at = None
    row.actual_start = None
    for key, val in overrides.items():
        setattr(row, key, val)
    return row


class TestNextFireTime:
    def test_future_time_today(self) -> None:
        now = datetime(2024, 4, 5, 10, 0, 0, tzinfo=UTC)
        result = _next_fire_time(now, 19, 0)
        assert result.hour == 19
        assert result.day == 5

    def test_past_time_goes_to_tomorrow(self) -> None:
        now = datetime(2024, 4, 5, 20, 0, 0, tzinfo=UTC)
        result = _next_fire_time(now, 19, 0)
        assert result.hour == 19
        assert result.day == 6


class TestPostMeetingCapture:
    @pytest.mark.asyncio
    async def test_prompts_for_ended_meeting(self) -> None:
        db = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchall = AsyncMock(return_value=[
            ("evt-1", "Team standup"),
        ])
        db.connection.execute = AsyncMock(return_value=cursor_mock)

        service = AsyncMock()
        service.dispatch = AsyncMock()

        capture = PostMeetingCapture(db, service, user_id="nick", delay_minutes=5)
        await capture._check_ended_meetings(datetime.now(tz=UTC))

        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert call_kwargs["notification_type"] == "post_meeting"
        assert "Team standup" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_skips_already_prompted(self) -> None:
        db = AsyncMock()
        cursor_mock = AsyncMock()
        cursor_mock.fetchall = AsyncMock(return_value=[
            ("evt-1", "Team standup"),
        ])
        db.connection.execute = AsyncMock(return_value=cursor_mock)

        service = AsyncMock()
        capture = PostMeetingCapture(db, service, user_id="nick")
        capture._prompted_events.add("evt-1")

        await capture._check_ended_meetings(datetime.now(tz=UTC))

        service.dispatch.assert_not_called()


class TestEveningCheckin:
    @pytest.mark.asyncio
    async def test_fire_sends_checkin(self) -> None:
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[])
        service = AsyncMock()
        service.dispatch = AsyncMock()

        checkin = EveningCheckin(db, service, user_id="nick", hour=19, minute=0)
        await checkin._fire()

        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert call_kwargs["notification_type"] == "evening_checkin"

    @pytest.mark.asyncio
    async def test_fire_includes_tomorrow_preview(self) -> None:
        from datetime import timedelta

        tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
        task = _make_task_row(
            scheduled_start=f"{tomorrow}T09:00:00",
            status="scheduled",
            title="Morning standup",
        )
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[task])
        service = AsyncMock()
        service.dispatch = AsyncMock()

        checkin = EveningCheckin(db, service, user_id="nick")
        await checkin._fire()

        service.dispatch.assert_called_once()
        embed = service.dispatch.call_args[1]["embed"]
        assert "Morning standup" in embed.description


class TestStaleTaskDetector:
    @pytest.mark.asyncio
    async def test_flags_old_backlog_tasks(self) -> None:
        old_task = _make_task_row(
            created_at="2024-01-01T10:00:00",
            status="backlog",
            scheduled_start=None,
        )
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[old_task])
        service = AsyncMock()
        service.dispatch = AsyncMock()

        detector = StaleTaskDetector(db, service, user_id="nick", stale_days=7)
        await detector._check()

        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert call_kwargs["notification_type"] == "stale_task"

    @pytest.mark.asyncio
    async def test_skips_scheduled_tasks(self) -> None:
        task = _make_task_row(
            created_at="2024-01-01T10:00:00",
            status="backlog",
            scheduled_start="2024-04-10T10:00:00",
        )
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[task])
        service = AsyncMock()

        detector = StaleTaskDetector(db, service, user_id="nick", stale_days=7)
        await detector._check()

        service.dispatch.assert_not_called()


class TestAfternoonInactivityCheck:
    @pytest.mark.asyncio
    async def test_nudges_when_no_activity(self) -> None:
        task = _make_task_row(
            created_at="2024-01-01T10:00:00",
            status="backlog",
            completed_at=None,
            actual_start=None,
            scheduled_start=None,
        )
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[task])
        service = AsyncMock()
        service.dispatch = AsyncMock()

        check = AfternoonInactivityCheck(db, service, user_id="nick")
        await check._fire()

        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert call_kwargs["notification_type"] == "afternoon_inactivity"

    @pytest.mark.asyncio
    async def test_skips_when_task_created_today(self) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        task = _make_task_row(
            created_at=f"{today}T10:00:00",
            status="backlog",
        )
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[task])
        service = AsyncMock()

        check = AfternoonInactivityCheck(db, service, user_id="nick")
        await check._fire()

        service.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_task_completed_today(self) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        task = _make_task_row(
            created_at="2024-01-01T10:00:00",
            completed_at=f"{today}T15:00:00",
            status="done",
        )
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[task])
        service = AsyncMock()

        check = AfternoonInactivityCheck(db, service, user_id="nick")
        await check._fire()

        service.dispatch.assert_not_called()
