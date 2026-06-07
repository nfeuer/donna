"""Regression: a time-bound task schedules even if the Challenger never resolves."""

from datetime import UTC, datetime, timedelta

import pytest

from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import NoSlotFoundError, ScheduledSlot
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.db_models import TaskStatus


class _FakeScheduler:
    def find_next_slot(self, task, events, now=None):
        return ScheduledSlot(start=datetime(2026, 6, 7, 14, tzinfo=UTC),
                             end=datetime(2026, 6, 7, 14, 30, tzinfo=UTC))


class _NoSlotScheduler:
    def find_next_slot(self, task, events, now=None):
        raise NoSlotFoundError(task.id, 14)


class _FakeDB:
    def __init__(self):
        self.transitions = []
        self.updates = {}

    async def transition_task_state(self, task_id, status):
        self.transitions.append(status)

    async def update_task(self, task_id, **kw):
        self.updates.update(kw)


def _task(**over):
    class T:
        id = "t1"
        status = TaskStatus.BACKLOG.value
        domain = "personal"
        priority = 2
        estimated_duration = 30
        title = "Send invoices"
        deadline = None
        deadline_type = "none"
        time_intent_json = TimeIntent(
            kind="exact", due_at=datetime(2026, 6, 7, tzinfo=UTC), strictness="hard"
        ).to_json()
    t = T()
    for k, v in over.items():
        setattr(t, k, v)
    return t


@pytest.mark.asyncio
async def test_time_bound_task_schedules_without_challenger_resolution():
    db = _FakeDB()
    auto = AutoScheduler(_FakeScheduler(), db, None, "primary", None)
    # challenger_pending=True simulates the old defer signal — it must be IGNORED
    # for a time-bound task now.
    await auto.on_task_created(_task(), challenger_pending=True)
    assert TaskStatus.SCHEDULED in db.transitions
    assert "scheduled_start" in db.updates


@pytest.mark.asyncio
async def test_no_time_task_stays_in_backlog_not_auto_scheduled():
    db = _FakeDB()
    auto = AutoScheduler(_FakeScheduler(), db, None, "primary", None)
    none_intent = TimeIntent(kind="none").to_json()
    await auto.on_task_created(_task(time_intent_json=none_intent), challenger_pending=False)
    # Undated tasks are NOT crammed onto the calendar — they wait in backlog.
    assert TaskStatus.SCHEDULED not in db.transitions
    assert "scheduled_start" not in db.updates


@pytest.mark.asyncio
async def test_bare_deadline_without_time_intent_still_schedules():
    """A deadline with no time_intent (legacy/app path) must schedule, not strand."""
    db = _FakeDB()
    auto = AutoScheduler(_FakeScheduler(), db, None, "primary", None)
    task = _task(
        time_intent_json=TimeIntent(kind="none").to_json(),
        deadline="2026-06-09T17:00:00+00:00",
        deadline_type="hard",
    )
    await auto.on_task_created(task, challenger_pending=True)
    assert TaskStatus.SCHEDULED in db.transitions


@pytest.mark.asyncio
async def test_no_slot_transitions_to_needs_scheduling():
    """A time-bound task that can't be slotted surfaces as needs_scheduling."""
    db = _FakeDB()
    auto = AutoScheduler(_NoSlotScheduler(), db, None, "primary", None)
    await auto.on_task_created(_task())
    assert TaskStatus.NEEDS_SCHEDULING in db.transitions
    assert TaskStatus.SCHEDULED not in db.transitions
