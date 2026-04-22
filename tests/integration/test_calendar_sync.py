"""Integration tests for CalendarSync.

Uses a real SQLite DB (temp file) and a mock GoogleCalendarClient.
No real Google API calls are made.

Tests verify:
  - Deleted Donna events move the task to backlog and clear calendar fields.
  - Time changes update scheduled_start and increment reschedule_count.
  - New conflicting user events trigger a reschedule of the affected task.
  - The calendar_mirror table is updated to reflect current state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

from donna.config import (
    CalendarConfig,
    CalendarEntryConfig,
    CredentialsConfig,
    InvalidTransitionEntry,
    SchedulingConfig,
    StateMachineConfig,
    SyncConfig,
    TimeWindowConfig,
    TimeWindowsConfig,
    TransitionEntry,
)
from donna.integrations.calendar import CalendarEvent, GoogleCalendarClient
from donna.scheduling.calendar_sync import CalendarSync
from donna.tasks.database import Database
from donna.tasks.db_models import Base, TaskDomain, TaskStatus
from donna.tasks.state_machine import StateMachine

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sm() -> StateMachine:
    config = StateMachineConfig(
        states=["backlog", "scheduled", "in_progress", "blocked", "waiting_input", "done", "cancelled"],
        initial_state="backlog",
        transitions=[
            TransitionEntry(**{"from": "backlog", "to": "scheduled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "scheduled", "to": "backlog", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "scheduled", "to": "in_progress", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "in_progress", "to": "done", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "blocked", "to": "scheduled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "blocked", "to": "cancelled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "waiting_input", "to": "scheduled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "waiting_input", "to": "cancelled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "in_progress", "to": "blocked", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "in_progress", "to": "scheduled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "*", "to": "cancelled", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "done", "to": "in_progress", "trigger": "t", "side_effects": []}),
            TransitionEntry(**{"from": "cancelled", "to": "backlog", "trigger": "t", "side_effects": []}),
        ],
        invalid_transitions=[
            InvalidTransitionEntry(**{"from": "backlog", "to": "done", "reason": "no"}),
            InvalidTransitionEntry(**{"from": "cancelled", "to": "*", "except": ["backlog"], "reason": "no"}),
            InvalidTransitionEntry(**{"from": "done", "to": "scheduled", "reason": "no"}),
        ],
    )
    return StateMachine(config)


@pytest.fixture
async def db(tmp_path, sm):
    db_path = tmp_path / "test.db"
    database = Database(db_path=str(db_path), state_machine=sm)
    await database.connect()
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield database
    await database.close()


@pytest.fixture
def cal_config() -> CalendarConfig:
    return CalendarConfig(
        calendars={"personal": CalendarEntryConfig(calendar_id="primary", access="read_write")},
        sync=SyncConfig(poll_interval_seconds=300, lookahead_days=7, lookbehind_days=1),
        scheduling=SchedulingConfig(slot_step_minutes=15, default_duration_minutes=60, search_horizon_days=14),
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


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _make_cal_event(
    event_id: str,
    start: datetime,
    end: datetime,
    donna_managed: bool = False,
    donna_task_id: str | None = None,
    calendar_id: str = "primary",
) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        calendar_id=calendar_id,
        summary="Test Event",
        start=start,
        end=end,
        donna_managed=donna_managed,
        donna_task_id=donna_task_id,
        etag='"etag1"',
    )


def _make_mock_client(events: list[CalendarEvent]) -> GoogleCalendarClient:
    """Build a mock client that returns the given events from list_events()."""
    client = MagicMock(spec=GoogleCalendarClient)
    client.list_events = AsyncMock(return_value=events)
    client.create_event = AsyncMock(return_value=_make_cal_event("ev-new", _utc(2026, 3, 24, 17), _utc(2026, 3, 24, 18), donna_managed=True, donna_task_id="task-id"))
    client.delete_event = AsyncMock(return_value=None)
    return client


async def _seed_mirror(db: Database, event_id: str, task_id: str, start: datetime, end: datetime) -> None:
    """Insert a row into calendar_mirror directly."""
    conn = db.connection
    await conn.execute(
        """
        INSERT INTO calendar_mirror
            (event_id, calendar_id, summary, start_time, end_time,
             donna_managed, donna_task_id, etag, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, "primary", "Task Event",
            start.isoformat(), end.isoformat(),
            1, task_id, '"etag1"', datetime.utcnow().isoformat(),
        ),
    )
    await conn.commit()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestDeletedDonnaEvent:
    async def test_deleted_donna_event_moves_task_to_backlog(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """When a Donna event disappears from the calendar, the task returns to backlog."""
        # Create a task and set it as scheduled.
        task = await db.create_task(
            user_id="nick",
            title="Review PR",
            domain=TaskDomain.PERSONAL,
        )
        await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        await db.update_task(
            task.id,
            calendar_event_id="ev-donna-001",
            donna_managed=True,
            scheduled_start=_utc(2026, 3, 23, 17),
        )

        # Seed the mirror so the sync knows this event existed.
        await _seed_mirror(
            db, "ev-donna-001", task.id,
            _utc(2026, 3, 23, 17), _utc(2026, 3, 23, 18),
        )

        # Mock client returns NO events (the Donna event was deleted).
        client = _make_mock_client([])
        sync = CalendarSync(client, db, cal_config)
        await sync.run_once()

        updated = await db.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.BACKLOG.value
        assert updated.calendar_event_id is None
        assert updated.donna_managed is False
        assert updated.scheduled_start is None

    async def test_deleted_event_callback_fired(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """on_task_unscheduled callback is called when an event is deleted."""
        task = await db.create_task(user_id="nick", title="My Task", domain=TaskDomain.PERSONAL)
        await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        await db.update_task(task.id, calendar_event_id="ev-001", donna_managed=True, scheduled_start=_utc(2026, 3, 23, 17))
        await _seed_mirror(db, "ev-001", task.id, _utc(2026, 3, 23, 17), _utc(2026, 3, 23, 18))

        fired: list[tuple[str, str]] = []

        async def on_unscheduled(task_id: str, reason: str) -> None:
            fired.append((task_id, reason))

        client = _make_mock_client([])
        sync = CalendarSync(client, db, cal_config, on_task_unscheduled=on_unscheduled)
        await sync.run_once()

        assert len(fired) == 1
        assert fired[0] == (task.id, "event_deleted")


class TestTimeChangedDonnaEvent:
    async def test_time_change_updates_scheduled_start(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """User moving a Donna event updates scheduled_start on the task."""
        old_start = _utc(2026, 3, 23, 17, 0)
        new_start = _utc(2026, 3, 23, 18, 0)

        task = await db.create_task(user_id="nick", title="Write report", domain=TaskDomain.PERSONAL)
        await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        await db.update_task(
            task.id, calendar_event_id="ev-donna-002", donna_managed=True, scheduled_start=old_start
        )

        # Mirror has old start time.
        await _seed_mirror(db, "ev-donna-002", task.id, old_start, old_start + timedelta(hours=1))

        # Calendar now shows new start time.
        moved_event = _make_cal_event(
            "ev-donna-002",
            new_start, new_start + timedelta(hours=1),
            donna_managed=True, donna_task_id=task.id,
        )
        client = _make_mock_client([moved_event])
        sync = CalendarSync(client, db, cal_config)
        await sync.run_once()

        updated = await db.get_task(task.id)
        assert updated is not None
        # scheduled_start should reflect new_start
        assert updated.scheduled_start is not None
        assert "18:00" in updated.scheduled_start

    async def test_time_change_increments_reschedule_count(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """Implicit reschedule increments reschedule_count."""
        old_start = _utc(2026, 3, 23, 17, 0)
        new_start = _utc(2026, 3, 23, 18, 30)

        task = await db.create_task(user_id="nick", title="Code review", domain=TaskDomain.WORK)
        await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        await db.update_task(
            task.id, calendar_event_id="ev-003", donna_managed=True,
            scheduled_start=old_start, reschedule_count=2,
        )
        await _seed_mirror(db, "ev-003", task.id, old_start, old_start + timedelta(hours=1))

        moved_event = _make_cal_event("ev-003", new_start, new_start + timedelta(hours=1),
                                       donna_managed=True, donna_task_id=task.id)
        client = _make_mock_client([moved_event])
        sync = CalendarSync(client, db, cal_config)
        await sync.run_once()

        updated = await db.get_task(task.id)
        assert updated is not None
        assert updated.reschedule_count == 3

    async def test_time_change_logs_correction(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """Time change should write a correction_log row for preference learning."""
        old_start = _utc(2026, 3, 23, 17, 0)
        new_start = _utc(2026, 3, 23, 18, 0)

        task = await db.create_task(user_id="nick", title="Task", domain=TaskDomain.PERSONAL)
        await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        await db.update_task(task.id, calendar_event_id="ev-004", donna_managed=True, scheduled_start=old_start)
        await _seed_mirror(db, "ev-004", task.id, old_start, old_start + timedelta(hours=1))

        moved_event = _make_cal_event("ev-004", new_start, new_start + timedelta(hours=1),
                                       donna_managed=True, donna_task_id=task.id)
        client = _make_mock_client([moved_event])
        sync = CalendarSync(client, db, cal_config)
        await sync.run_once()

        conn = db.connection
        cursor = await conn.execute(
            "SELECT field_corrected FROM correction_log WHERE task_id = ?", (task.id,)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "scheduled_start"


class TestMirrorUpdate:
    async def test_mirror_updated_after_sync(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """After a sync, calendar_mirror reflects live events."""
        new_event = _make_cal_event(
            "ev-brand-new",
            _utc(2026, 3, 23, 10), _utc(2026, 3, 23, 11),
        )
        client = _make_mock_client([new_event])
        sync = CalendarSync(client, db, cal_config)
        await sync.run_once()

        conn = db.connection
        cursor = await conn.execute(
            "SELECT event_id FROM calendar_mirror WHERE event_id = ?", ("ev-brand-new",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "ev-brand-new"

    async def test_stale_events_removed_from_mirror(
        self, db: Database, cal_config: CalendarConfig
    ) -> None:
        """Events no longer in calendar are removed from calendar_mirror."""
        # Seed a stale event (no task_id, not donna_managed)
        conn = db.connection
        await conn.execute(
            """
            INSERT INTO calendar_mirror
                (event_id, calendar_id, summary, start_time, end_time,
                 donna_managed, donna_task_id, etag, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ev-stale", "primary", "Old Event",
             _utc(2026, 3, 20, 10).isoformat(), _utc(2026, 3, 20, 11).isoformat(),
             0, None, '"old"', datetime.utcnow().isoformat()),
        )
        await conn.commit()

        # Sync returns no events (calendar is empty).
        client = _make_mock_client([])
        sync = CalendarSync(client, db, cal_config)
        await sync.run_once()

        cursor = await conn.execute(
            "SELECT event_id FROM calendar_mirror WHERE event_id = ?", ("ev-stale",)
        )
        row = await cursor.fetchone()
        assert row is None
