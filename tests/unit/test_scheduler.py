"""Unit tests for the scheduling engine.

Tests are pure (no DB, no network). The scheduler's find_next_slot() is
synchronous; all tests call it directly with a fixed 'now' and a list of
pre-built CalendarEvent stubs.

Time constraint rules tested:
  - Blackout (12am–6am): absolute, blocks even priority 5.
  - Quiet hours (8pm–midnight): soft, blocks priority < 5.
  - Work window (8am–5pm, Mon–Fri): required for work domain tasks.
  - Personal window (5pm–8pm, any day): required for personal/family tasks.
  - Weekend window (6am–8pm, Sat–Sun): allowed for personal/family tasks.
  - Calendar overlap: slots skip over existing events.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from donna.scheduling.scheduler import NoSlotFoundError, ScheduledSlot, Scheduler
from donna.tasks.database import TaskRow


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def cal_config() -> CalendarConfig:
    """Minimal CalendarConfig matching config/calendar.yaml defaults."""
    return CalendarConfig(
        calendars={
            "personal": CalendarEntryConfig(calendar_id="primary", access="read_write"),
        },
        sync=SyncConfig(poll_interval_seconds=300, lookahead_days=7, lookbehind_days=1),
        scheduling=SchedulingConfig(
            slot_step_minutes=15,
            default_duration_minutes=60,
            search_horizon_days=14,
        ),
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


@pytest.fixture
def scheduler(cal_config: CalendarConfig) -> Scheduler:
    return Scheduler(cal_config)


def _make_task(
    domain: str = "personal",
    priority: int = 2,
    estimated_duration: int | None = 60,
    status: str = "backlog",
) -> TaskRow:
    """Build a minimal TaskRow for scheduling tests."""
    return TaskRow(
        id="task-uuid-001",
        user_id="nick",
        title="Test task",
        description=None,
        domain=domain,
        priority=priority,
        status=status,
        estimated_duration=estimated_duration,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=None,
        parent_task=None,
        prep_work_flag=False,
        prep_work_instructions=None,
        agent_eligible=False,
        assigned_agent=None,
        agent_status=None,
        tags=None,
        notes=None,
        reschedule_count=0,
        created_at="2026-03-20T09:00:00",
        created_via="discord",
        estimated_cost=None,
        calendar_event_id=None,
        donna_managed=False,
    )


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _event(
    start: datetime,
    end: datetime,
    event_id: str = "ev-001",
    donna_managed: bool = False,
) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        calendar_id="primary",
        summary="Existing event",
        start=start,
        end=end,
        donna_managed=donna_managed,
        donna_task_id=None,
        etag="abc",
    )


# ------------------------------------------------------------------
# Tests: basic slot finding
# ------------------------------------------------------------------


class TestFindNextSlot:
    def test_finds_slot_in_personal_window(self, scheduler: Scheduler) -> None:
        """Personal task scheduled starting at 5pm on a weekday."""
        task = _make_task(domain="personal", estimated_duration=60)
        # now = Monday 2026-03-23 09:00 UTC (weekday 0)
        now = _utc(2026, 3, 23, 9, 0)  # Mon — personal window starts at 17:00
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.hour >= 17
        assert slot.end - slot.start == timedelta(hours=1)

    def test_finds_slot_in_work_window(self, scheduler: Scheduler) -> None:
        """Work task scheduled within work hours (8am–5pm, Mon–Fri)."""
        task = _make_task(domain="work", estimated_duration=60)
        # now = Monday 2026-03-23 06:00 UTC — before work window opens
        now = _utc(2026, 3, 23, 6, 0)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.hour >= 8
        assert slot.start.weekday() in [0, 1, 2, 3, 4]
        assert slot.end.hour <= 17

    def test_respects_duration(self, scheduler: Scheduler) -> None:
        task = _make_task(domain="personal", estimated_duration=90)
        now = _utc(2026, 3, 23, 9, 0)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.end - slot.start == timedelta(minutes=90)

    def test_uses_default_duration_when_none(self, scheduler: Scheduler) -> None:
        task = _make_task(domain="personal", estimated_duration=None)
        now = _utc(2026, 3, 23, 9, 0)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.end - slot.start == timedelta(minutes=60)  # default from config

    def test_slot_at_15min_boundary(self, scheduler: Scheduler) -> None:
        """Slots must start at 15-minute boundaries."""
        task = _make_task(domain="personal", estimated_duration=60)
        # now = Monday 17:07 — should round up to 17:15
        now = _utc(2026, 3, 23, 17, 7)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.minute % 15 == 0


# ------------------------------------------------------------------
# Tests: blackout (absolute)
# ------------------------------------------------------------------


class TestBlackout:
    def test_blackout_blocks_all_domains(self, scheduler: Scheduler) -> None:
        """No task of any domain may be placed during 12am–6am."""
        for domain in ("personal", "work", "family"):
            task = _make_task(domain=domain, estimated_duration=60)
            # now = 01:00 UTC Saturday — deep in blackout
            now = _utc(2026, 3, 28, 1, 0)  # Saturday
            slot = scheduler.find_next_slot(task, [], now=now)
            # Slot must start at or after 6am
            assert slot.start.hour >= 6, f"Domain {domain} scheduled during blackout"

    def test_blackout_blocks_priority5(self, scheduler: Scheduler) -> None:
        """Blackout is absolute — even priority 5 cannot be scheduled during it."""
        task = _make_task(domain="personal", priority=5, estimated_duration=60)
        now = _utc(2026, 3, 28, 2, 0)  # 2am Saturday
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.hour >= 6

    def test_slot_starting_at_6am_is_allowed(self, scheduler: Scheduler) -> None:
        """6am is the first valid minute (blackout ends at 6)."""
        task = _make_task(domain="personal", estimated_duration=60)
        # Saturday 5:59 — should find a slot at 6:00 or later
        now = _utc(2026, 3, 28, 5, 59)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.hour >= 6


# ------------------------------------------------------------------
# Tests: quiet hours (8pm–midnight, soft)
# ------------------------------------------------------------------


class TestQuietHours:
    def test_quiet_hours_block_lower_priority(self, scheduler: Scheduler) -> None:
        """Priority < 5 tasks cannot be scheduled during 8pm–midnight."""
        task = _make_task(domain="personal", priority=3, estimated_duration=60)
        # Monday 20:00 — entering quiet hours
        now = _utc(2026, 3, 23, 20, 0)
        slot = scheduler.find_next_slot(task, [], now=now)
        # Must skip to next day's personal window (17:00+)
        assert not (slot.start.hour >= 20), (
            "Priority-3 task was scheduled during quiet hours"
        )

    def test_quiet_hours_allow_priority5(self, scheduler: Scheduler) -> None:
        """Priority 5 tasks may be scheduled during quiet hours (8pm–midnight)."""
        task = _make_task(domain="personal", priority=5, estimated_duration=60)
        # Monday 20:00 — quiet hours open
        now = _utc(2026, 3, 23, 20, 0)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.hour >= 20


# ------------------------------------------------------------------
# Tests: domain windows
# ------------------------------------------------------------------


class TestDomainWindows:
    def test_work_task_not_placed_outside_work_hours(self, scheduler: Scheduler) -> None:
        """Work tasks must not be placed during personal/evening hours."""
        task = _make_task(domain="work", estimated_duration=60)
        # Monday 17:30 — outside work window (8–17)
        now = _utc(2026, 3, 23, 17, 30)
        slot = scheduler.find_next_slot(task, [], now=now)
        # Must skip to next working day 8am+
        assert slot.start.hour >= 8
        assert slot.start.weekday() in [0, 1, 2, 3, 4]

    def test_personal_task_on_weekend(self, scheduler: Scheduler) -> None:
        """Personal tasks can be scheduled on weekends (6am–8pm)."""
        task = _make_task(domain="personal", estimated_duration=60)
        # Saturday 10:00
        now = _utc(2026, 3, 28, 10, 0)
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.weekday() in [5, 6]
        assert slot.start.hour >= 6

    def test_family_task_in_personal_window(self, scheduler: Scheduler) -> None:
        """Family tasks can go in personal window."""
        task = _make_task(domain="family", estimated_duration=30)
        now = _utc(2026, 3, 23, 9, 0)  # Monday
        slot = scheduler.find_next_slot(task, [], now=now)
        assert slot.start.hour >= 17 or slot.start.weekday() in [5, 6]


# ------------------------------------------------------------------
# Tests: calendar overlap avoidance
# ------------------------------------------------------------------


class TestCalendarOverlap:
    def test_skips_overlapping_event(self, scheduler: Scheduler) -> None:
        """Slot must not overlap existing calendar events."""
        task = _make_task(domain="personal", estimated_duration=60)
        now = _utc(2026, 3, 23, 17, 0)  # Mon 17:00
        # Block the first valid slot (17:00–18:00)
        block = _event(_utc(2026, 3, 23, 17, 0), _utc(2026, 3, 23, 18, 0))
        slot = scheduler.find_next_slot(task, [block], now=now)
        # Should be scheduled at 18:00 or later
        assert slot.start >= _utc(2026, 3, 23, 18, 0)

    def test_skips_multiple_consecutive_events(self, scheduler: Scheduler) -> None:
        """Should skip a contiguous block of events."""
        task = _make_task(domain="personal", estimated_duration=60)
        now = _utc(2026, 3, 23, 17, 0)
        events = [
            _event(_utc(2026, 3, 23, 17, 0), _utc(2026, 3, 23, 18, 0), "ev1"),
            _event(_utc(2026, 3, 23, 18, 0), _utc(2026, 3, 23, 19, 0), "ev2"),
        ]
        slot = scheduler.find_next_slot(task, events, now=now)
        assert slot.start >= _utc(2026, 3, 23, 19, 0)

    def test_no_overlap_with_donna_event(self, scheduler: Scheduler) -> None:
        """Donna-managed events are still treated as conflicts."""
        task = _make_task(domain="personal", estimated_duration=60)
        now = _utc(2026, 3, 23, 17, 0)
        donna_event = _event(
            _utc(2026, 3, 23, 17, 0), _utc(2026, 3, 23, 18, 0), donna_managed=True
        )
        slot = scheduler.find_next_slot(task, [donna_event], now=now)
        assert slot.start >= _utc(2026, 3, 23, 18, 0)


# ------------------------------------------------------------------
# Tests: error cases
# ------------------------------------------------------------------


class TestNoSlot:
    def test_raises_when_horizon_exhausted(self, scheduler: Scheduler) -> None:
        """NoSlotFoundError is raised when no slot exists within the horizon."""
        # Use a very short horizon config
        from donna.config import SchedulingConfig as SC

        short_config = CalendarConfig(
            calendars=scheduler._config.calendars,
            sync=scheduler._config.sync,
            scheduling=SC(slot_step_minutes=15, default_duration_minutes=60, search_horizon_days=1),
            time_windows=scheduler._config.time_windows,
            credentials=scheduler._config.credentials,
        )
        tight_scheduler = Scheduler(short_config)
        task = _make_task(domain="work", estimated_duration=60)
        # Friday 16:30 — work ends at 17, horizon is 1 day, so very few work slots
        # Fill remaining work time with an event to force failure
        now = _utc(2026, 3, 27, 16, 30)  # Friday
        block = _event(_utc(2026, 3, 27, 16, 30), _utc(2026, 3, 28, 23, 59))
        # Also block the next Monday (beyond 1-day horizon), so no work slot available
        with pytest.raises(NoSlotFoundError) as exc_info:
            tight_scheduler.find_next_slot(task, [block], now=now)
        assert exc_info.value.task_id == task.id
