from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from donna.api.routes.calendar_week import _week_bounds, get_calendar_week


@dataclass(frozen=True)
class FakeCalendarEvent:
    event_id: str
    calendar_id: str
    summary: str
    start: datetime
    end: datetime
    donna_managed: bool
    donna_task_id: str | None
    etag: str
    attendees: tuple = ()


@dataclass
class FakeTask:
    id: str
    title: str
    scheduled_start: str
    estimated_duration: int
    priority: int
    domain: str
    donna_managed: bool
    status: str = "scheduled"


class TestWeekBounds:
    def test_midweek_date_returns_monday_to_sunday(self) -> None:
        tz = ZoneInfo("America/New_York")
        start, end = _week_bounds(date(2026, 5, 13), tz)  # Wednesday
        assert start == datetime(2026, 5, 11, 0, 0, 0, tzinfo=tz)  # Monday
        assert end == datetime(2026, 5, 17, 23, 59, 59, tzinfo=tz)  # Sunday

    def test_monday_returns_same_week(self) -> None:
        tz = ZoneInfo("America/New_York")
        start, end = _week_bounds(date(2026, 5, 11), tz)
        assert start.date() == date(2026, 5, 11)
        assert end.date() == date(2026, 5, 17)

    def test_sunday_returns_same_week(self) -> None:
        tz = ZoneInfo("America/New_York")
        start, end = _week_bounds(date(2026, 5, 17), tz)
        assert start.date() == date(2026, 5, 11)
        assert end.date() == date(2026, 5, 17)


class TestGetCalendarWeek:
    @pytest.fixture
    def tz(self) -> ZoneInfo:
        return ZoneInfo("America/New_York")

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        req = MagicMock()
        req.app.state.calendar_timezone = "America/New_York"
        req.app.state.calendar_ids = ["personal"]
        req.app.state.calendar_client = AsyncMock()
        req.app.state.db = AsyncMock()
        return req

    @pytest.mark.asyncio
    async def test_merges_google_and_donna_events(
        self, mock_request: MagicMock, tz: ZoneInfo
    ) -> None:
        gcal_event = FakeCalendarEvent(
            event_id="abc",
            calendar_id="personal",
            summary="Team standup",
            start=datetime(2026, 5, 13, 9, 0, tzinfo=tz),
            end=datetime(2026, 5, 13, 9, 30, tzinfo=tz),
            donna_managed=False,
            donna_task_id=None,
            etag="e1",
        )
        mock_request.app.state.calendar_client.list_events.return_value = [gcal_event]

        donna_task = FakeTask(
            id="42",
            title="Review proposals",
            scheduled_start="2026-05-13T10:30:00-04:00",
            estimated_duration=3600,
            priority=2,
            domain="work",
            donna_managed=True,
        )

        from donna.tasks.db_models import TaskStatus
        async def _list_tasks(user_id: str, status: TaskStatus) -> list:
            if status == TaskStatus.SCHEDULED:
                return [donna_task]
            return []
        mock_request.app.state.db.list_tasks.side_effect = _list_tasks

        result = await get_calendar_week(
            request=mock_request,
            user_id="nick",
            ref_date="2026-05-13",
        )

        assert result["count"] == 2
        assert result["events"][0]["source"] == "google"
        assert result["events"][0]["title"] == "Team standup"
        assert result["events"][1]["source"] == "donna"
        assert result["events"][1]["title"] == "Review proposals"
        assert result["events"][1]["priority"] == 2
        assert result["events"][1]["status"] == "scheduled"

    @pytest.mark.asyncio
    async def test_returns_donna_only_when_calendar_unavailable(
        self, mock_request: MagicMock, tz: ZoneInfo
    ) -> None:
        mock_request.app.state.calendar_client = None

        donna_task = FakeTask(
            id="42",
            title="Review proposals",
            scheduled_start="2026-05-13T10:30:00-04:00",
            estimated_duration=3600,
            priority=2,
            domain="work",
            donna_managed=True,
        )

        from donna.tasks.db_models import TaskStatus
        async def _list_tasks(user_id: str, status: TaskStatus) -> list:
            if status == TaskStatus.SCHEDULED:
                return [donna_task]
            return []
        mock_request.app.state.db.list_tasks.side_effect = _list_tasks

        result = await get_calendar_week(
            request=mock_request,
            user_id="nick",
            ref_date="2026-05-13",
        )

        assert result["count"] == 1
        assert result["events"][0]["source"] == "donna"
        assert "google_calendar_unavailable" in result["warnings"]

    @pytest.mark.asyncio
    async def test_donna_end_computed_from_duration(
        self, mock_request: MagicMock, tz: ZoneInfo
    ) -> None:
        mock_request.app.state.calendar_client = None

        donna_task = FakeTask(
            id="99",
            title="Quick task",
            scheduled_start="2026-05-14T14:00:00-04:00",
            estimated_duration=1800,  # 30 minutes
            priority=3,
            domain="personal",
            donna_managed=True,
        )

        from donna.tasks.db_models import TaskStatus
        async def _list_tasks(user_id: str, status: TaskStatus) -> list:
            if status == TaskStatus.SCHEDULED:
                return [donna_task]
            return []
        mock_request.app.state.db.list_tasks.side_effect = _list_tasks

        result = await get_calendar_week(
            request=mock_request,
            user_id="nick",
            ref_date="2026-05-14",
        )

        event = result["events"][0]
        assert event["start"] == "2026-05-14T14:00:00-04:00"
        assert event["end"] == "2026-05-14T14:30:00-04:00"
