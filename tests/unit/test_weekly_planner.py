"""Unit tests for WeeklyPlanner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.scheduling.weekly_planner import WeeklyPlanner, _next_monday_fire
from donna.tasks.database import TaskRow


def _task(task_id="t1", priority=3, status="backlog", deadline=None, created_at=None) -> TaskRow:
    return TaskRow(
        id=task_id, user_id="nick", title=f"Task {task_id}", description=None,
        domain="work", priority=priority, status=status, estimated_duration=60,
        deadline=deadline, deadline_type="none", scheduled_start=None,
        actual_start=None, completed_at=None, recurrence=None, dependencies=None,
        parent_task=None, prep_work_flag=False, prep_work_instructions=None,
        agent_eligible=False, assigned_agent=None, agent_status=None,
        tags=None, notes=None, reschedule_count=0,
        created_at=created_at or "2026-04-01T00:00:00", created_via="discord",
        estimated_cost=None, calendar_event_id=None, donna_managed=False,
        nudge_count=0, quality_score=None,
    )


def _make_planner(db=None, scheduler=None, recalculator=None, service=None,
                  calendar_client=None):
    db = db or MagicMock()
    scheduler = scheduler or MagicMock()
    recalculator = recalculator or MagicMock()
    recalculator.recalculate_and_apply = AsyncMock(return_value=[])
    service = service or MagicMock()
    service.dispatch = AsyncMock()
    calendar_client = calendar_client or MagicMock()
    return WeeklyPlanner(
        db=db,
        scheduler=scheduler,
        recalculator=recalculator,
        service=service,
        calendar_client=calendar_client,
        calendar_id="primary",
        user_id="nick",
    )


# --- _next_monday_fire tests ---

def test_next_monday_fire_from_tuesday():
    """From Tuesday, next fire is next Monday."""
    tuesday = datetime(2026, 4, 7, 10, 0, tzinfo=UTC)  # Tuesday
    fire = _next_monday_fire(tuesday, 8, 0)
    assert fire.weekday() == 0  # Monday
    assert fire > tuesday


def test_next_monday_fire_from_monday_before_fire_time():
    """From Monday before fire time, fire is today."""
    monday = datetime(2026, 4, 6, 7, 0, tzinfo=UTC)  # Monday 7:00
    fire = _next_monday_fire(monday, 8, 0)
    assert fire == datetime(2026, 4, 6, 8, 0, tzinfo=UTC)


def test_next_monday_fire_from_monday_after_fire_time():
    """From Monday after fire time, fire is next Monday."""
    monday = datetime(2026, 4, 6, 9, 0, tzinfo=UTC)  # Monday 9:00, after 8:00
    fire = _next_monday_fire(monday, 8, 0)
    assert fire == datetime(2026, 4, 13, 8, 0, tzinfo=UTC)


# --- _select_candidates tests ---

def test_candidates_include_high_priority_tasks():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)
    tasks = [
        _task("high", priority=3, status="backlog"),
        _task("low", priority=1, status="backlog"),
    ]
    candidates = planner._select_candidates(tasks, now)
    assert any(t.id == "high" for t in candidates)
    assert not any(t.id == "low" for t in candidates)


def test_candidates_include_tasks_with_deadline_this_week():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)
    deadline_this_week = (now + timedelta(days=5)).isoformat()
    tasks = [
        _task("deadline_task", priority=1, status="backlog", deadline=deadline_this_week),
    ]
    candidates = planner._select_candidates(tasks, now)
    assert any(t.id == "deadline_task" for t in candidates)


def test_candidates_include_stale_backlog_tasks():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)
    # Created 10 days ago (stale)
    old_date = (now - timedelta(days=10)).isoformat()
    tasks = [
        _task("stale", priority=1, status="backlog", created_at=old_date),
    ]
    candidates = planner._select_candidates(tasks, now)
    assert any(t.id == "stale" for t in candidates)


def test_candidates_excludes_non_backlog():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)
    tasks = [
        _task("done_task", priority=5, status="done"),
        _task("scheduled_task", priority=5, status="scheduled"),
    ]
    candidates = planner._select_candidates(tasks, now)
    assert candidates == []


# --- handle_plan_reply tests ---

@pytest.mark.asyncio
async def test_confirm_reply_calls_apply_proposal():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)

    task = _task("t1", priority=3)
    from donna.scheduling.scheduler import ScheduledSlot
    slot = ScheduledSlot(
        start=now + timedelta(hours=2),
        end=now + timedelta(hours=3),
    )

    proposal_id = "test-proposal"
    planner._pending[proposal_id] = {
        "tasks": [task],
        "slots": [slot],
        "expires_at": now + timedelta(hours=10),
    }
    planner._apply_proposal = AsyncMock()

    handled = await planner.handle_plan_reply("confirm", now=now)
    assert handled is True
    planner._apply_proposal.assert_called_once_with(proposal_id)


@pytest.mark.asyncio
async def test_skip_reply_removes_task_from_proposal():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)

    task_a = _task("a")
    task_a = task_a.__class__(**{**task_a.__dict__, "title": "Oil change"})  # type: ignore
    task_b = _task("b")
    task_b = task_b.__class__(**{**task_b.__dict__, "title": "Write report"})  # type: ignore

    from donna.scheduling.scheduler import ScheduledSlot
    slot = ScheduledSlot(start=now + timedelta(hours=2), end=now + timedelta(hours=3))

    proposal_id = "test-prop"
    planner._pending[proposal_id] = {
        "tasks": [task_a, task_b],
        "slots": [slot, slot],
        "expires_at": now + timedelta(hours=10),
    }

    handled = await planner.handle_plan_reply("skip Oil change", now=now)
    assert handled is True
    remaining = [t.id for t in planner._pending[proposal_id]["tasks"]]
    assert "b" in remaining
    assert "a" not in remaining


@pytest.mark.asyncio
async def test_no_pending_proposal_returns_false():
    planner = _make_planner()
    handled = await planner.handle_plan_reply("confirm")
    assert handled is False


@pytest.mark.asyncio
async def test_expired_proposals_cleaned_up():
    planner = _make_planner()
    now = datetime(2026, 4, 6, 8, 0, tzinfo=UTC)

    # Insert an already-expired proposal.
    planner._pending["old"] = {
        "tasks": [],
        "slots": [],
        "expires_at": now - timedelta(hours=1),
    }
    handled = await planner.handle_plan_reply("confirm", now=now)
    assert handled is False
    assert "old" not in planner._pending
