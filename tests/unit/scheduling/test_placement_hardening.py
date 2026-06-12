"""Tests for the placement-hardening bundle (Fable Scheduling S1).

Covers the choke-point fixes in ``Scheduler.schedule_task``:
  #5 — busy-set is the union of ALL configured calendars, and a read failure
       fails CLOSED (abort) instead of booking blind against an empty calendar.
  #8 — the read→find→create section is serialized by an instance lock.

See docs/superpowers/specs/2026-06-11-scheduling-fable-critique-design.md.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

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
from donna.integrations.calendar import CalendarEvent
from donna.scheduling.scheduler import CalendarReadError, Scheduler
from donna.tasks.database import TaskRow


def _cfg() -> CalendarConfig:
    return CalendarConfig(
        calendars={
            "personal": CalendarEntryConfig(calendar_id="primary", access="read_write"),
            "work": CalendarEntryConfig(calendar_id="work-cal", access="read_only"),
            "family": CalendarEntryConfig(calendar_id="", access="read_only"),  # unset → skipped
        },
        sync=SyncConfig(),
        scheduling=SchedulingConfig(
            slot_step_minutes=15, default_duration_minutes=60, search_horizon_days=14
        ),
        time_windows=TimeWindowsConfig(
            blackout=TimeWindowConfig(start_hour=0, end_hour=6, days=[0, 1, 2, 3, 4, 5, 6]),
            quiet_hours=TimeWindowConfig(start_hour=20, end_hour=24, days=[0, 1, 2, 3, 4, 5, 6]),
            work=TimeWindowConfig(start_hour=8, end_hour=17, days=[0, 1, 2, 3, 4]),
            personal=TimeWindowConfig(start_hour=17, end_hour=20, days=[0, 1, 2, 3, 4, 5, 6]),
            weekend=TimeWindowConfig(start_hour=6, end_hour=20, days=[5, 6]),
        ),
        credentials=CredentialsConfig(
            client_secrets_path="c.json", token_path="t.json",
            scopes=["https://www.googleapis.com/auth/calendar"],
        ),
        timezone="UTC",
    )


def _task() -> TaskRow:
    return TaskRow(
        id="t1", user_id="nick", title="Test", description=None, domain="personal",
        priority=2, status="backlog", estimated_duration=60, deadline=None,
        deadline_type="none", scheduled_start=None, actual_start=None, completed_at=None,
        recurrence=None, dependencies=None, parent_task=None, prep_work_flag=False,
        prep_work_instructions=None, agent_eligible=False, assigned_agent=None,
        agent_status=None, tags=None, notes=None, reschedule_count=0,
        created_at="2026-03-20T09:00:00", created_via="discord", estimated_cost=None,
        calendar_event_id=None, donna_managed=False, nudge_count=0, quality_score=None,
    )


def _ev(start: datetime, end: datetime, cal: str) -> CalendarEvent:
    return CalendarEvent(
        event_id=f"ev-{cal}", calendar_id=cal, summary="busy", start=start, end=end,
        donna_managed=False, donna_task_id=None, etag="x",
    )


def _utc(h: int, m: int = 0, day: int = 23) -> datetime:
    # 2026-03-23 is a Monday.
    return datetime(2026, 3, day, h, m, tzinfo=UTC)


# ---------------------------------------------------------------------------
# #5 — all-calendars busy union + fail-closed
# ---------------------------------------------------------------------------


def test_read_calendar_ids_skips_empty() -> None:
    sched = Scheduler(_cfg())
    assert set(sched._read_calendar_ids()) == {"primary", "work-cal"}


@pytest.mark.asyncio
async def test_gather_busy_unions_all_calendars() -> None:
    sched = Scheduler(_cfg())
    client = MagicMock()
    client.list_events = AsyncMock(
        side_effect=lambda cal, a, b: [_ev(_utc(17), _utc(18), cal)]
    )
    busy = await sched._gather_busy(client, _utc(9), _utc(23, 59))
    assert client.list_events.await_count == 2  # primary + work-cal (family skipped)
    assert {e.calendar_id for e in busy} == {"primary", "work-cal"}


@pytest.mark.asyncio
async def test_schedule_task_fails_closed_on_read_error() -> None:
    """A calendar read failure must ABORT, never book against an empty calendar."""
    sched = Scheduler(_cfg())
    client = MagicMock()
    client.list_events = AsyncMock(side_effect=RuntimeError("google 500"))
    client.create_event = AsyncMock()
    db = MagicMock()

    with pytest.raises(CalendarReadError):
        await sched.schedule_task(_task(), db, client, "primary")

    client.create_event.assert_not_called()  # never booked blind


@pytest.mark.asyncio
async def test_schedule_task_avoids_work_calendar_meeting() -> None:
    """A work-calendar meeting must block the personal placement (cross-calendar)."""
    sched = Scheduler(_cfg())
    client = MagicMock()

    async def _list(cal: str, a: datetime, b: datetime) -> list[CalendarEvent]:
        # The personal window opens 17:00; a meeting on the WORK calendar
        # occupies 17:00–18:00. Placement must skip past it.
        if cal == "work-cal":
            return [_ev(_utc(17), _utc(18), "work-cal")]
        return []

    client.list_events = AsyncMock(side_effect=_list)
    created = _ev(_utc(18), _utc(19), "primary")
    client.create_event = AsyncMock(return_value=created)
    client.delete_event = AsyncMock()
    db = MagicMock()
    db.transition_task_state = AsyncMock()
    db.update_task = AsyncMock()

    task = _task()
    slot = await sched.schedule_task(task, db, client, "primary", )

    # Placed at or after 18:00 — the 17:00 work meeting was respected.
    assert slot.start >= _utc(18)
    client.create_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# #8 — placement section is serialized by a lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_task_serialized_by_lock() -> None:
    """Two concurrent placements must not run the read→create section in parallel."""
    sched = Scheduler(_cfg())
    in_section = 0
    max_parallel = 0

    async def _list(cal: str, a: datetime, b: datetime) -> list[CalendarEvent]:
        nonlocal in_section, max_parallel
        in_section += 1
        max_parallel = max(max_parallel, in_section)
        await asyncio.sleep(0.01)  # widen the window for a race to show
        in_section -= 1
        return []

    client = MagicMock()
    client.list_events = AsyncMock(side_effect=_list)
    client.create_event = AsyncMock(side_effect=lambda **kw: _ev(_utc(17), _utc(18), "primary"))
    client.delete_event = AsyncMock()
    db = MagicMock()
    db.transition_task_state = AsyncMock()
    db.update_task = AsyncMock()

    await asyncio.gather(
        sched.schedule_task(_task(), db, client, "primary"),
        sched.schedule_task(_task(), db, client, "primary"),
    )

    assert max_parallel == 1  # the lock serialized them
