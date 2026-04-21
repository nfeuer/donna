"""Tests for calendar_read skill-system tool."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.tools.calendar_read import CalendarReadError, calendar_read


class FakeCalendarEvent:
    def __init__(
        self, *, event_id: str, summary: str, start: datetime, end: datetime,
        donna_managed: bool = False, donna_task_id: str | None = None,
    ):
        self.event_id = event_id
        self.summary = summary
        self.start = start
        self.end = end
        self.donna_managed = donna_managed
        self.donna_task_id = donna_task_id
        self.calendar_id = "primary"
        self.etag = "etag-1"


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.list_events = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_calendar_read_returns_events(fake_client):
    fake_client.list_events.return_value = [
        FakeCalendarEvent(
            event_id="e1",
            summary="Morning standup",
            start=datetime(2026, 4, 21, 9, 0, tzinfo=UTC),
            end=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
            donna_managed=True,
            donna_task_id="task-42",
        ),
    ]
    out = await calendar_read(
        client=fake_client,
        time_min="2026-04-21T00:00:00+00:00",
        time_max="2026-04-22T00:00:00+00:00",
    )
    assert out["ok"] is True
    assert len(out["events"]) == 1
    e = out["events"][0]
    assert e["event_id"] == "e1"
    assert e["summary"] == "Morning standup"
    assert e["start"] == "2026-04-21T09:00:00+00:00"
    assert e["end"] == "2026-04-21T09:30:00+00:00"
    assert e["donna_managed"] is True
    assert e["donna_task_id"] == "task-42"


@pytest.mark.asyncio
async def test_calendar_read_defaults_to_primary(fake_client):
    fake_client.list_events.return_value = []
    await calendar_read(
        client=fake_client,
        time_min="2026-04-21T00:00:00Z",
        time_max="2026-04-22T00:00:00Z",
    )
    assert fake_client.list_events.call_args.kwargs["calendar_id"] == "primary"


@pytest.mark.asyncio
async def test_calendar_read_accepts_z_suffix(fake_client):
    fake_client.list_events.return_value = []
    out = await calendar_read(
        client=fake_client,
        time_min="2026-04-21T00:00:00Z",
        time_max="2026-04-22T00:00:00Z",
    )
    assert out["ok"] is True
    kwargs = fake_client.list_events.call_args.kwargs
    assert kwargs["time_min"].tzinfo is not None
    assert kwargs["time_max"].tzinfo is not None


@pytest.mark.asyncio
async def test_calendar_read_missing_time_raises(fake_client):
    with pytest.raises(CalendarReadError):
        await calendar_read(client=fake_client, time_min="", time_max="2026-04-22T00:00:00Z")


@pytest.mark.asyncio
async def test_calendar_read_bad_iso_raises(fake_client):
    with pytest.raises(CalendarReadError):
        await calendar_read(
            client=fake_client, time_min="not-a-date", time_max="2026-04-22T00:00:00Z",
        )


@pytest.mark.asyncio
async def test_calendar_read_reversed_window_raises(fake_client):
    with pytest.raises(CalendarReadError):
        await calendar_read(
            client=fake_client,
            time_min="2026-04-22T00:00:00Z",
            time_max="2026-04-21T00:00:00Z",
        )


@pytest.mark.asyncio
async def test_calendar_read_propagates_client_failure(fake_client):
    fake_client.list_events.side_effect = RuntimeError("token expired")
    with pytest.raises(CalendarReadError):
        await calendar_read(
            client=fake_client,
            time_min="2026-04-21T00:00:00Z",
            time_max="2026-04-22T00:00:00Z",
        )


@pytest.mark.asyncio
async def test_calendar_read_never_calls_write_methods(fake_client):
    fake_client.create_event = AsyncMock()
    fake_client.update_event = AsyncMock()
    fake_client.delete_event = AsyncMock()
    fake_client.list_events.return_value = []
    await calendar_read(
        client=fake_client,
        time_min="2026-04-21T00:00:00Z",
        time_max="2026-04-22T00:00:00Z",
    )
    fake_client.create_event.assert_not_called()
    fake_client.update_event.assert_not_called()
    fake_client.delete_event.assert_not_called()
