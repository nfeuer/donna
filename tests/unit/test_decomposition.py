"""Unit tests for DecompositionService."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.agents.decomposition import DecompositionService, DecomposeResult
from donna.tasks.database import TaskRow


def _make_task(**kwargs) -> TaskRow:
    defaults = dict(
        id="task-1",
        user_id="nick",
        title="Write annual report",
        description="Q4 annual report",
        domain="work",
        priority=3,
        status="backlog",
        estimated_duration=120,
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
    )
    defaults.update(kwargs)
    return TaskRow(**defaults)


def _make_service(db=None, router=None):
    db = db or MagicMock()
    router = router or MagicMock()
    project_root = Path("/tmp")
    svc = DecompositionService(db, router, "nick", project_root)
    return svc


@pytest.mark.asyncio
async def test_decompose_raises_for_missing_task():
    svc = _make_service()
    svc._db.get_task = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="Task not found"):
        await svc.decompose("nonexistent-id")


@pytest.mark.asyncio
async def test_decompose_creates_subtasks():
    task = _make_task()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)

    subtask_1 = _make_task(id="sub-1", title="Research data", parent_task="task-1")
    subtask_2 = _make_task(id="sub-2", title="Write draft", parent_task="task-1")
    db.create_task = AsyncMock(side_effect=[subtask_1, subtask_2])
    db.update_task = AsyncMock(return_value=None)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})

    llm_response = {
        "assessment": "Moderate complexity",
        "subtasks": [
            {"title": "Research data", "description": "gather", "estimated_duration": 60,
             "priority_order": 1, "agent_eligible": False, "dependencies": []},
            {"title": "Write draft", "description": "draft", "estimated_duration": 60,
             "priority_order": 2, "agent_eligible": False, "dependencies": [0]},
        ],
        "total_estimated_hours": 2.0,
        "missing_information": [],
        "suggested_deadline_feasible": True,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    with patch("donna.agents.decomposition.validate_output", return_value=llm_response):
        svc = _make_service(db=db, router=router)
        svc._render_prompt = MagicMock(return_value="prompt text")
        result = await svc.decompose("task-1")

    assert len(result.subtask_ids) == 2
    assert result.parent_task_id == "task-1"
    assert result.total_estimated_hours == 2.0
    assert result.deadline_feasible is True


@pytest.mark.asyncio
async def test_decompose_sets_parent_task_on_subtasks():
    task = _make_task()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)

    created = _make_task(id="sub-1", parent_task="task-1")
    db.create_task = AsyncMock(return_value=created)
    db.update_task = AsyncMock(return_value=None)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    llm_response = {
        "assessment": "Simple",
        "subtasks": [
            {"title": "Step 1", "description": "do it", "estimated_duration": 30,
             "priority_order": 1, "agent_eligible": False, "dependencies": []},
        ],
        "total_estimated_hours": 0.5,
        "missing_information": [],
        "suggested_deadline_feasible": True,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    with patch("donna.agents.decomposition.validate_output", return_value=llm_response):
        svc = _make_service(db=db, router=router)
        svc._render_prompt = MagicMock(return_value="prompt")
        await svc.decompose("task-1")

    # Verify parent_task was passed to create_task.
    create_call = db.create_task.call_args
    assert create_call.kwargs.get("parent_task") == "task-1"


@pytest.mark.asyncio
async def test_decompose_resolves_dependency_indices_to_uuids():
    """Subtask with dependency index 0 should get UUID of first subtask."""
    task = _make_task()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)

    sub1 = _make_task(id="uuid-sub-1", parent_task="task-1")
    sub2 = _make_task(id="uuid-sub-2", parent_task="task-1")
    db.create_task = AsyncMock(side_effect=[sub1, sub2])
    db.update_task = AsyncMock(return_value=None)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    llm_response = {
        "assessment": "Multi-step",
        "subtasks": [
            {"title": "First", "description": "first", "estimated_duration": 30,
             "priority_order": 1, "agent_eligible": False, "dependencies": []},
            {"title": "Second", "description": "second", "estimated_duration": 30,
             "priority_order": 2, "agent_eligible": False, "dependencies": [0]},
        ],
        "total_estimated_hours": 1.0,
        "missing_information": [],
        "suggested_deadline_feasible": True,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    with patch("donna.agents.decomposition.validate_output", return_value=llm_response):
        svc = _make_service(db=db, router=router)
        svc._render_prompt = MagicMock(return_value="prompt")
        await svc.decompose("task-1")

    # Second subtask should have dependencies=[uuid-sub-1].
    update_calls = db.update_task.call_args_list
    dep_update = [c for c in update_calls if "dependencies" in c.kwargs]
    assert len(dep_update) == 1
    assert dep_update[0].kwargs["dependencies"] == ["uuid-sub-1"]


@pytest.mark.asyncio
async def test_decompose_missing_information_returned():
    task = _make_task()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)
    db.create_task = AsyncMock(return_value=_make_task(id="sub-1", parent_task="task-1"))
    db.update_task = AsyncMock(return_value=None)

    router = MagicMock()
    router.get_output_schema = MagicMock(return_value={})
    llm_response = {
        "assessment": "Needs clarification",
        "subtasks": [
            {"title": "Step", "description": "step", "estimated_duration": 30,
             "priority_order": 1, "agent_eligible": False, "dependencies": []},
        ],
        "total_estimated_hours": 0.5,
        "missing_information": [{"question": "What format?", "blocking": True}],
        "suggested_deadline_feasible": None,
    }
    router.complete = AsyncMock(return_value=(llm_response, MagicMock()))

    with patch("donna.agents.decomposition.validate_output", return_value=llm_response):
        svc = _make_service(db=db, router=router)
        svc._render_prompt = MagicMock(return_value="prompt")
        result = await svc.decompose("task-1")

    assert len(result.missing_information) == 1
    assert result.missing_information[0]["question"] == "What format?"
