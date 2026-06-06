"""Regression: a time-bound task schedules even if the Challenger never resolves."""

from datetime import UTC, datetime, timedelta

import pytest

from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.db_models import TaskStatus


class _FakeScheduler:
    def find_next_slot(self, task, events, now=None):
        return ScheduledSlot(start=datetime(2026, 6, 7, 14, tzinfo=UTC),
                             end=datetime(2026, 6, 7, 14, 30, tzinfo=UTC))


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
