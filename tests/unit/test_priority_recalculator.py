"""Unit tests for PriorityRecalculator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import PriorityConfig
from donna.scheduling.priority_engine import PriorityEngine
from donna.scheduling.priority_recalculator import PriorityRecalculator, _next_fire_time
from donna.tasks.database import TaskRow


def _task(task_id="t1", priority=2, status="backlog", deadline=None, deadline_type="none"):
    return TaskRow(
        id=task_id, user_id="nick", title="Task", description=None,
        domain="work", priority=priority, status=status,
        estimated_duration=60, deadline=deadline, deadline_type=deadline_type,
        scheduled_start=None, actual_start=None, completed_at=None,
        recurrence=None, dependencies=None, parent_task=None,
        prep_work_flag=False, prep_work_instructions=None,
        agent_eligible=False, assigned_agent=None, agent_status=None,
        tags=None, notes=None, reschedule_count=0,
        created_at="2026-04-01T00:00:00", created_via="discord",
        estimated_cost=None, calendar_event_id=None, donna_managed=False,
        nudge_count=0, quality_score=None,
    )


def _make_recalculator(db=None, engine=None, service=None):
    db = db or MagicMock()
    engine = engine or PriorityEngine(PriorityConfig())
    service = service or MagicMock()
    service.dispatch = AsyncMock()
    return PriorityRecalculator(db, engine, service, "nick")


def test_next_fire_time_same_day_future():
    now = datetime(2026, 4, 2, 5, 30, tzinfo=UTC)
    fire = _next_fire_time(now, 6, 0)
    assert fire == datetime(2026, 4, 2, 6, 0, tzinfo=UTC)


def test_next_fire_time_past_today():
    now = datetime(2026, 4, 2, 7, 0, tzinfo=UTC)
    fire = _next_fire_time(now, 6, 0)
    assert fire == datetime(2026, 4, 3, 6, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_recalculate_and_apply_updates_changed_tasks():
    now = datetime(2026, 4, 2, 6, 0, tzinfo=UTC)
    deadline = (now + timedelta(days=2)).isoformat()
    task = _task("t1", priority=2, deadline=deadline, deadline_type="hard")

    db = MagicMock()
    db.list_tasks = AsyncMock(return_value=[task])
    db.update_task = AsyncMock(return_value=task)

    config = PriorityConfig(deadline_warning_days=3, deadline_critical_days=1)
    engine = PriorityEngine(config)
    rec = _make_recalculator(db=db, engine=engine)

    changes = await rec.recalculate_and_apply(now)

    assert len(changes) == 1
    assert changes[0] == ("t1", 2, 4)
    db.update_task.assert_called_once_with("t1", priority=4)


@pytest.mark.asyncio
async def test_recalculate_skips_done_tasks():
    now = datetime(2026, 4, 2, 6, 0, tzinfo=UTC)
    deadline = (now + timedelta(hours=10)).isoformat()
    task = _task("t1", priority=2, deadline=deadline, deadline_type="hard", status="done")

    db = MagicMock()
    db.list_tasks = AsyncMock(return_value=[task])
    db.update_task = AsyncMock()

    rec = _make_recalculator(db=db)
    changes = await rec.recalculate_and_apply(now)

    assert changes == []
    db.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_recalculate_no_changes_no_updates():
    now = datetime(2026, 4, 2, 6, 0, tzinfo=UTC)
    task = _task("t1", priority=2)

    db = MagicMock()
    db.list_tasks = AsyncMock(return_value=[task])
    db.update_task = AsyncMock()

    rec = _make_recalculator(db=db)
    changes = await rec.recalculate_and_apply(now)

    assert changes == []
    db.update_task.assert_not_called()
