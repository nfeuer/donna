"""Unit tests for PriorityEngine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from donna.config import PriorityConfig
from donna.scheduling.priority_engine import PriorityEngine
from donna.tasks.database import TaskRow


def _config(**kwargs):
    defaults = dict(
        deadline_warning_days=3,
        deadline_critical_days=1,
        workload_threshold_per_day=5,
        escalation_after_reschedules=1,
    )
    defaults.update(kwargs)
    return PriorityConfig(**defaults)


def _task(**kwargs) -> TaskRow:
    defaults = dict(
        id="t1",
        user_id="nick",
        title="Task",
        description=None,
        domain="work",
        priority=2,
        status="backlog",
        estimated_duration=60,
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
        created_at="2026-04-01T00:00:00",
        created_via="discord",
        estimated_cost=None,
        calendar_event_id=None,
        donna_managed=False,
        nudge_count=0,
        quality_score=None,
    )
    defaults.update(kwargs)
    return TaskRow(**defaults)


NOW = datetime(2026, 4, 2, 6, 0, tzinfo=UTC)


def test_hard_deadline_within_warning_days_floors_to_4():
    engine = PriorityEngine(_config())
    deadline = NOW + timedelta(days=2)
    task = _task(priority=2, deadline_type="hard", deadline=deadline.isoformat())
    changes = engine.recalculate([task], now=NOW)
    assert len(changes) == 1
    assert changes[0] == ("t1", 2, 4)


def test_hard_deadline_within_critical_days_floors_to_5():
    engine = PriorityEngine(_config())
    deadline = NOW + timedelta(hours=18)
    task = _task(priority=2, deadline_type="hard", deadline=deadline.isoformat())
    changes = engine.recalculate([task], now=NOW)
    assert len(changes) == 1
    assert changes[0] == ("t1", 2, 5)


def test_soft_deadline_not_escalated():
    engine = PriorityEngine(_config())
    deadline = NOW + timedelta(days=2)
    task = _task(priority=2, deadline_type="soft", deadline=deadline.isoformat())
    changes = engine.recalculate([task], now=NOW)
    assert changes == []


def test_no_deadline_unchanged():
    engine = PriorityEngine(_config())
    task = _task(priority=2, deadline=None)
    changes = engine.recalculate([task], now=NOW)
    assert changes == []


def test_already_high_priority_not_changed():
    engine = PriorityEngine(_config())
    deadline = NOW + timedelta(days=2)
    task = _task(priority=5, deadline_type="hard", deadline=deadline.isoformat())
    changes = engine.recalculate([task], now=NOW)
    assert changes == []


def test_done_tasks_excluded():
    engine = PriorityEngine(_config())
    deadline = NOW + timedelta(hours=10)
    task = _task(priority=2, deadline_type="hard", deadline=deadline.isoformat(), status="done")
    changes = engine.recalculate([task], now=NOW)
    assert changes == []


def test_workload_pressure_escalates_rescheduled_task():
    engine = PriorityEngine(_config(workload_threshold_per_day=2, escalation_after_reschedules=1))
    sched = "2026-04-03T09:00:00"
    # The task itself + 2 more = 3 total on same day > threshold of 2.
    task = _task(id="main", priority=2, scheduled_start=sched, reschedule_count=2)
    other1 = _task(id="o1", priority=2, scheduled_start=sched)
    other2 = _task(id="o2", priority=2, scheduled_start=sched)
    changes = engine.recalculate([task, other1, other2], now=NOW)
    main_changes = [c for c in changes if c[0] == "main"]
    assert main_changes == [("main", 2, 3)]


def test_workload_pressure_below_threshold_unchanged():
    engine = PriorityEngine(_config(workload_threshold_per_day=5, escalation_after_reschedules=1))
    sched = "2026-04-03T09:00:00"
    task = _task(id="main", priority=2, scheduled_start=sched, reschedule_count=2)
    # Only 1 task on that day (below threshold=5).
    changes = engine.recalculate([task], now=NOW)
    assert changes == []


def test_deadline_and_workload_compound_correctly():
    """Deadline escalates to floor 4; workload bumps from base 2 to 3.
    Final priority = max(4, 3) = 4."""
    engine = PriorityEngine(_config(workload_threshold_per_day=1, escalation_after_reschedules=1))
    deadline = NOW + timedelta(days=2)  # within warning window → floor 4
    sched = "2026-04-03T09:00:00"
    task = _task(id="main", priority=2, deadline_type="hard",
                 deadline=deadline.isoformat(), scheduled_start=sched, reschedule_count=2)
    other = _task(id="o1", priority=2, scheduled_start=sched)  # 2 tasks > threshold=1
    changes = engine.recalculate([task, other], now=NOW)
    main_changes = [c for c in changes if c[0] == "main"]
    # deadline floor=4, workload bump from base 2→3; max(4, 3) = 4
    assert main_changes[0][2] == 4
