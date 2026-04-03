"""Unit tests for PrepAgent."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.agents.prep_agent import PrepAgent
from donna.tasks.database import TaskRow


def _make_task(**kwargs) -> TaskRow:
    defaults = dict(
        id="task-1",
        user_id="nick",
        title="Team presentation",
        description="Q4 results presentation",
        domain="work",
        priority=3,
        status="scheduled",
        estimated_duration=60,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=None,
        parent_task=None,
        prep_work_flag=True,
        prep_work_instructions="Research Q4 metrics",
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


def _make_agent(db=None, router=None, service=None):
    db = db or MagicMock()
    router = router or MagicMock()
    service = service or MagicMock()
    service.dispatch = AsyncMock()
    return PrepAgent(db, router, service, "nick", Path("/tmp"), lead_hours=2.0)


@pytest.mark.asyncio
async def test_executes_prep_for_task_in_window():
    """Task scheduled 1.5h from now → prep executed."""
    now = datetime(2026, 4, 2, 10, 0, tzinfo=UTC)
    start = now + timedelta(hours=1, minutes=30)
    task = _make_task(scheduled_start=start.isoformat(), agent_status=None)

    db = MagicMock()
    db.update_task = AsyncMock(return_value=task)
    db.get_task = AsyncMock(return_value=task)

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=AsyncMock(fetchall=AsyncMock(return_value=[(task.id,)])))
    db.connection = conn

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    llm_response = {
        "summary": "Research complete",
        "sections": [],
        "action_items": ["Check metrics"],
        "open_questions": [],
        "tools_used": [],
        "time_spent_minutes": 5,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    agent = _make_agent(db=db, router=router)
    agent._render_prompt = MagicMock(return_value="prompt")

    with patch("donna.agents.prep_agent.validate_output", return_value=llm_response):
        await agent._check_and_execute(now)

    db.update_task.assert_called()


@pytest.mark.asyncio
async def test_skips_task_already_in_progress():
    """Task with agent_status=IN_PROGRESS is not returned by the DB query."""
    now = datetime(2026, 4, 2, 10, 0, tzinfo=UTC)

    db = MagicMock()
    conn = AsyncMock()
    # Return empty — the SQL filters out IN_PROGRESS tasks
    conn.execute = AsyncMock(return_value=AsyncMock(fetchall=AsyncMock(return_value=[])))
    db.connection = conn

    agent = _make_agent(db=db)
    # _execute_prep should not be called
    agent._execute_prep = AsyncMock()
    await agent._check_and_execute(now)
    agent._execute_prep.assert_not_called()


@pytest.mark.asyncio
async def test_agent_status_set_to_complete_on_success():
    task = _make_task(scheduled_start="2026-04-02T11:30:00+00:00", agent_status=None)

    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)
    db.update_task = AsyncMock(return_value=task)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    llm_response = {
        "summary": "Done",
        "sections": [],
        "action_items": [],
        "open_questions": [],
        "tools_used": [],
        "time_spent_minutes": 2,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    agent = _make_agent(db=db, router=router)
    agent._render_prompt = MagicMock(return_value="prompt")

    with patch("donna.agents.prep_agent.validate_output", return_value=llm_response):
        await agent._execute_prep(task)

    # Find the call that sets agent_status=complete
    calls = db.update_task.call_args_list
    status_calls = [c for c in calls if c.kwargs.get("agent_status") == "complete"]
    assert len(status_calls) == 1


@pytest.mark.asyncio
async def test_agent_status_set_to_failed_on_error():
    """If the LLM call raises, agent_status is set to 'failed'."""
    task = _make_task(scheduled_start="2026-04-02T11:30:00+00:00", agent_status=None)

    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)
    db.update_task = AsyncMock(return_value=task)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    router.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    agent = _make_agent(db=db, router=router)
    agent._render_prompt = MagicMock(return_value="prompt")
    await agent._execute_prep(task)

    calls = db.update_task.call_args_list
    fail_calls = [c for c in calls if c.kwargs.get("agent_status") == "failed"]
    assert len(fail_calls) == 1


@pytest.mark.asyncio
async def test_prep_summary_appended_to_notes():
    """Prep summary is appended to the task notes JSON array."""
    existing_notes = ["existing note"]
    task = _make_task(
        scheduled_start="2026-04-02T11:30:00+00:00",
        agent_status=None,
        notes=json.dumps(existing_notes),
    )

    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)
    db.update_task = AsyncMock(return_value=task)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    llm_response = {
        "summary": "Key metrics ready",
        "sections": [],
        "action_items": ["Review slide 3"],
        "open_questions": [],
        "tools_used": [],
        "time_spent_minutes": 5,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    agent = _make_agent(db=db, router=router)
    agent._render_prompt = MagicMock(return_value="prompt")

    with patch("donna.agents.prep_agent.validate_output", return_value=llm_response):
        await agent._execute_prep(task)

    calls = db.update_task.call_args_list
    note_calls = [c for c in calls if "notes" in c.kwargs]
    assert len(note_calls) == 1
    notes = note_calls[0].kwargs["notes"]
    assert isinstance(notes, list)
    assert len(notes) == 2  # existing + new
    assert "[prep_research]:" in notes[1]
