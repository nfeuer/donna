"""Unit tests for the Google Calendar client.

Tests use a mock service object injected via the ``service`` parameter —
no real Google API calls are made.

Verifies:
  - list_events() pages through results and returns CalendarEvent objects.
  - create_event() sets donnaManaged and donnaTaskId extended properties.
  - update_event() sends a PATCH with the new times.
  - delete_event() calls the delete endpoint.
  - CalendarEvent is populated correctly from API responses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from donna.config import (
    CalendarConfig,
    CalendarEntryConfig,
    CredentialsConfig,
    SchedulingConfig,
    SyncConfig,
    TimeWindowConfig,
    TimeWindowsConfig,
)
from donna.integrations.calendar import (
    _PROP_MANAGED,
    _PROP_TASK_ID,
    GoogleCalendarClient,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _make_raw_event(
    event_id: str = "ev-001",
    summary: str = "Meeting",
    start_iso: str = "2026-03-23T10:00:00+00:00",
    end_iso: str = "2026-03-23T11:00:00+00:00",
    donna_managed: bool = False,
    donna_task_id: str | None = None,
    etag: str = '"etag1"',
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if donna_managed or donna_task_id:
        private: dict[str, str] = {}
        if donna_managed:
            private[_PROP_MANAGED] = "true"
        if donna_task_id:
            private[_PROP_TASK_ID] = donna_task_id
        props = {"extendedProperties": {"private": private}}

    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "etag": etag,
        **props,
    }


def _make_service(list_response: dict | None = None) -> MagicMock:
    """Build a minimal mock Google Calendar service."""
    svc = MagicMock()
    events_mock = svc.events.return_value
    if list_response is not None:
        events_mock.list.return_value.execute.return_value = list_response
    return svc


@pytest.fixture
def config() -> CalendarConfig:
    return CalendarConfig(
        calendars={"personal": CalendarEntryConfig(calendar_id="primary", access="read_write")},
        sync=SyncConfig(),
        scheduling=SchedulingConfig(),
        time_windows=TimeWindowsConfig(
            blackout=TimeWindowConfig(start_hour=0, end_hour=6, days=[0, 1, 2, 3, 4, 5, 6]),
            quiet_hours=TimeWindowConfig(start_hour=20, end_hour=24, days=[0, 1, 2, 3, 4, 5, 6]),
            work=TimeWindowConfig(start_hour=8, end_hour=17, days=[0, 1, 2, 3, 4]),
            personal=TimeWindowConfig(start_hour=17, end_hour=20, days=[0, 1, 2, 3, 4, 5, 6]),
            weekend=TimeWindowConfig(start_hour=6, end_hour=20, days=[5, 6]),
        ),
        credentials=CredentialsConfig(
            client_secrets_path="credentials.json",
            token_path="token.json",
            scopes=["https://www.googleapis.com/auth/calendar"],
        ),
    )


# ------------------------------------------------------------------
# Tests: list_events
# ------------------------------------------------------------------


class TestListEvents:
    @pytest.mark.asyncio
    async def test_returns_calendar_events(self, config: CalendarConfig) -> None:
        raw = _make_raw_event("ev-001", "Stand-up")
        svc = _make_service({"items": [raw], "nextPageToken": None})
        client = GoogleCalendarClient(config, service=svc)

        events = await client.list_events(
            "primary", _utc(2026, 3, 23, 0), _utc(2026, 3, 24, 0)
        )

        assert len(events) == 1
        assert events[0].event_id == "ev-001"
        assert events[0].summary == "Stand-up"

    @pytest.mark.asyncio
    async def test_parses_donna_extended_properties(self, config: CalendarConfig) -> None:
        raw = _make_raw_event("ev-002", donna_managed=True, donna_task_id="task-uuid-999")
        svc = _make_service({"items": [raw], "nextPageToken": None})
        client = GoogleCalendarClient(config, service=svc)

        events = await client.list_events(
            "primary", _utc(2026, 3, 23, 0), _utc(2026, 3, 24, 0)
        )

        assert events[0].donna_managed is True
        assert events[0].donna_task_id == "task-uuid-999"

    @pytest.mark.asyncio
    async def test_paginates_multiple_pages(self, config: CalendarConfig) -> None:
        raw1 = _make_raw_event("ev-001", "Event 1")
        raw2 = _make_raw_event("ev-002", "Event 2")

        svc = MagicMock()
        # First call returns page 1 with nextPageToken, second call returns page 2.
        svc.events.return_value.list.return_value.execute.side_effect = [
            {"items": [raw1], "nextPageToken": "token-abc"},
            {"items": [raw2], "nextPageToken": None},
        ]
        client = GoogleCalendarClient(config, service=svc)

        events = await client.list_events(
            "primary", _utc(2026, 3, 23, 0), _utc(2026, 3, 25, 0)
        )

        assert len(events) == 2
        assert {e.event_id for e in events} == {"ev-001", "ev-002"}

    @pytest.mark.asyncio
    async def test_empty_calendar(self, config: CalendarConfig) -> None:
        svc = _make_service({"items": [], "nextPageToken": None})
        client = GoogleCalendarClient(config, service=svc)

        events = await client.list_events(
            "primary", _utc(2026, 3, 23, 0), _utc(2026, 3, 24, 0)
        )

        assert events == []

    @pytest.mark.asyncio
    async def test_calendar_id_propagated(self, config: CalendarConfig) -> None:
        raw = _make_raw_event("ev-001")
        svc = _make_service({"items": [raw], "nextPageToken": None})
        client = GoogleCalendarClient(config, service=svc)

        events = await client.list_events(
            "work-cal-id", _utc(2026, 3, 23, 0), _utc(2026, 3, 24, 0)
        )

        assert events[0].calendar_id == "work-cal-id"


# ------------------------------------------------------------------
# Tests: create_event
# ------------------------------------------------------------------


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_create_event_sets_donna_extended_properties(
        self, config: CalendarConfig
    ) -> None:
        task_id = "task-uuid-abc"
        returned_raw = _make_raw_event(
            "ev-new",
            donna_managed=True,
            donna_task_id=task_id,
        )
        svc = MagicMock()
        svc.events.return_value.insert.return_value.execute.return_value = returned_raw

        client = GoogleCalendarClient(config, service=svc)
        start = _utc(2026, 3, 23, 17, 0)
        end = _utc(2026, 3, 23, 18, 0)

        event = await client.create_event("primary", "My Task", start, end, task_id)

        # Verify the insert was called with correct extended properties.
        insert_call = svc.events.return_value.insert
        call_kwargs = insert_call.call_args[1]
        body = call_kwargs["body"]
        private = body["extendedProperties"]["private"]
        assert private[_PROP_MANAGED] == "true"
        assert private[_PROP_TASK_ID] == task_id

        # Verify the returned CalendarEvent.
        assert event.event_id == "ev-new"
        assert event.donna_managed is True
        assert event.donna_task_id == task_id

    @pytest.mark.asyncio
    async def test_create_event_sets_start_end(self, config: CalendarConfig) -> None:
        task_id = "task-uuid-001"
        returned_raw = _make_raw_event("ev-new", donna_managed=True, donna_task_id=task_id)
        svc = MagicMock()
        svc.events.return_value.insert.return_value.execute.return_value = returned_raw

        client = GoogleCalendarClient(config, service=svc)
        start = _utc(2026, 3, 23, 17, 0)
        end = _utc(2026, 3, 23, 18, 0)
        await client.create_event("primary", "Task", start, end, task_id)

        body = svc.events.return_value.insert.call_args[1]["body"]
        assert "2026-03-23T17:00:00" in body["start"]["dateTime"]
        assert "2026-03-23T18:00:00" in body["end"]["dateTime"]


# ------------------------------------------------------------------
# Tests: update_event
# ------------------------------------------------------------------


class TestUpdateEvent:
    @pytest.mark.asyncio
    async def test_update_event_patches_times(self, config: CalendarConfig) -> None:
        new_raw = _make_raw_event(
            "ev-001",
            start_iso="2026-03-24T09:00:00+00:00",
            end_iso="2026-03-24T10:00:00+00:00",
        )
        svc = MagicMock()
        svc.events.return_value.patch.return_value.execute.return_value = new_raw

        client = GoogleCalendarClient(config, service=svc)
        new_start = _utc(2026, 3, 24, 9, 0)
        new_end = _utc(2026, 3, 24, 10, 0)
        event = await client.update_event("primary", "ev-001", new_start, new_end)

        patch_call = svc.events.return_value.patch
        call_kwargs = patch_call.call_args[1]
        assert call_kwargs["eventId"] == "ev-001"
        body = call_kwargs["body"]
        assert "2026-03-24T09:00:00" in body["start"]["dateTime"]
        assert event.event_id == "ev-001"


# ------------------------------------------------------------------
# Tests: delete_event
# ------------------------------------------------------------------


class TestDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_event_calls_api(self, config: CalendarConfig) -> None:
        svc = MagicMock()
        svc.events.return_value.delete.return_value.execute.return_value = None

        client = GoogleCalendarClient(config, service=svc)
        await client.delete_event("primary", "ev-001")

        delete_call = svc.events.return_value.delete
        call_kwargs = delete_call.call_args[1]
        assert call_kwargs["calendarId"] == "primary"
        assert call_kwargs["eventId"] == "ev-001"


# ------------------------------------------------------------------
# Tests: _parse_event edge cases
# ------------------------------------------------------------------


class TestParseEvent:
    def test_parses_all_day_event(self, config: CalendarConfig) -> None:
        from donna.integrations.calendar import _parse_event

        raw = {
            "id": "ev-allday",
            "summary": "Birthday",
            "start": {"date": "2026-03-25"},
            "end": {"date": "2026-03-26"},
            "etag": '"xyz"',
        }
        event = _parse_event(raw, "primary")
        assert event.event_id == "ev-allday"
        assert event.donna_managed is False

    def test_non_donna_event_has_no_task_id(self, config: CalendarConfig) -> None:
        from donna.integrations.calendar import _parse_event

        raw = _make_raw_event("ev-user", donna_managed=False)
        event = _parse_event(raw, "primary")
        assert event.donna_managed is False
        assert event.donna_task_id is None
