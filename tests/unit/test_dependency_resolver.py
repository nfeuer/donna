"""Unit tests for dependency_resolver module."""

from __future__ import annotations

import pytest

from donna.scheduling.dependency_resolver import (
    CyclicDependencyError,
    tasks_ready_to_schedule,
    topological_sort,
)
from donna.tasks.database import TaskRow


def _task(task_id: str, deps: list[str] | None = None, status: str = "backlog") -> TaskRow:
    import json
    return TaskRow(
        id=task_id,
        user_id="nick",
        title=f"Task {task_id}",
        description=None,
        domain="work",
        priority=2,
        status=status,
        estimated_duration=60,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=json.dumps(deps) if deps else None,
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


def test_no_dependencies_preserves_order():
    tasks = [_task("A"), _task("B"), _task("C")]
    result = topological_sort(tasks)
    assert [t.id for t in result] == ["A", "B", "C"]


def test_linear_chain():
    """A→B→C means A before B before C."""
    a = _task("A")
    b = _task("B", deps=["A"])
    c = _task("C", deps=["B"])
    result = topological_sort([c, b, a])  # input in reverse order
    ids = [t.id for t in result]
    assert ids.index("A") < ids.index("B")
    assert ids.index("B") < ids.index("C")


def test_diamond_dependency():
    """A→B, A→C, B→D, C→D → A first, D last."""
    a = _task("A")
    b = _task("B", deps=["A"])
    c = _task("C", deps=["A"])
    d = _task("D", deps=["B", "C"])
    result = topological_sort([d, b, c, a])
    ids = [t.id for t in result]
    assert ids[0] == "A"
    assert ids[-1] == "D"


def test_cyclic_dependency_raises():
    """A depends on B, B depends on A → CyclicDependencyError."""
    a = _task("A", deps=["B"])
    b = _task("B", deps=["A"])
    with pytest.raises(CyclicDependencyError):
        topological_sort([a, b])


def test_cyclic_error_contains_cycle_ids():
    a = _task("A", deps=["B"])
    b = _task("B", deps=["A"])
    with pytest.raises(CyclicDependencyError) as exc_info:
        topological_sort([a, b])
    cycle = exc_info.value.cycle
    assert "A" in cycle or "B" in cycle


def test_tasks_ready_to_schedule_no_deps():
    """Tasks with no dependencies and in backlog status are ready."""
    tasks = [_task("A"), _task("B")]
    ready = tasks_ready_to_schedule(tasks)
    assert {t.id for t in ready} == {"A", "B"}


def test_tasks_ready_to_schedule_with_done_dep():
    """Task whose dep is done/scheduled is ready."""
    done_dep = _task("X", status="done")
    task = _task("A", deps=["X"])
    ready = tasks_ready_to_schedule([done_dep, task])
    assert any(t.id == "A" for t in ready)


def test_tasks_ready_to_schedule_with_pending_dep():
    """Task whose dep is in backlog is NOT ready."""
    dep = _task("X", status="backlog")
    task = _task("A", deps=["X"])
    ready = tasks_ready_to_schedule([dep, task])
    assert not any(t.id == "A" for t in ready)


def test_tasks_ready_excludes_resolved_tasks():
    """Done/cancelled tasks are not included in the ready list."""
    done = _task("A", status="done")
    cancelled = _task("B", status="cancelled")
    ready = tasks_ready_to_schedule([done, cancelled])
    assert ready == []
