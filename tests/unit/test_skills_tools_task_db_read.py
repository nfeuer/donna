"""Tests for task_db_read skill-system tool."""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.tools.task_db_read import task_db_read, TaskDbReadError
from donna.tasks.db_models import TaskDomain, TaskStatus


@dataclasses.dataclass(frozen=True)
class FakeTaskRow:
    id: str
    user_id: str = "nick"
    title: str = "sample"
    description: str | None = "body"
    domain: str = "personal"
    priority: int = 3
    status: str = "backlog"
    estimated_duration: int | None = None
    deadline: str | None = None
    deadline_type: str = "none"
    scheduled_start: str | None = None
    actual_start: str | None = None
    completed_at: str | None = None
    recurrence: str | None = None
    dependencies: str | None = None
    parent_task: str | None = None
    prep_work_flag: bool = False
    prep_work_instructions: str | None = None
    agent_eligible: bool = False
    assigned_agent: str | None = None
    agent_status: str | None = None
    tags: str | None = None
    notes: str | None = None
    reschedule_count: int = 0
    created_at: str = "2026-04-21T00:00:00+00:00"
    created_via: str = "discord"
    estimated_cost: float | None = None
    calendar_event_id: str | None = None
    donna_managed: bool = False
    nudge_count: int = 0
    quality_score: float | None = None
    capability_name: str | None = "generate_digest"
    inputs: dict | None = None


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.get_task = AsyncMock()
    c.list_tasks = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_task_db_read_by_id_returns_projection(fake_client):
    fake_client.get_task.return_value = FakeTaskRow(
        id="t1", title="Draft spec", capability_name="task_decompose",
    )
    out = await task_db_read(client=fake_client, task_id="t1")
    assert out["ok"] is True
    task = out["task"]
    assert task["id"] == "t1"
    assert task["title"] == "Draft spec"
    assert task["capability_name"] == "task_decompose"
    # Ensure internal bookkeeping fields are not leaked.
    assert "reschedule_count" not in task
    assert "created_via" not in task


@pytest.mark.asyncio
async def test_task_db_read_by_id_not_found_raises(fake_client):
    fake_client.get_task.return_value = None
    with pytest.raises(TaskDbReadError):
        await task_db_read(client=fake_client, task_id="missing")


@pytest.mark.asyncio
async def test_task_db_read_empty_task_id_raises(fake_client):
    with pytest.raises(TaskDbReadError):
        await task_db_read(client=fake_client, task_id="   ")


@pytest.mark.asyncio
async def test_task_db_read_task_id_excludes_filters(fake_client):
    with pytest.raises(TaskDbReadError):
        await task_db_read(client=fake_client, task_id="t1", user_id="nick")


@pytest.mark.asyncio
async def test_task_db_read_list_filters(fake_client):
    fake_client.list_tasks.return_value = [
        FakeTaskRow(id="t1"), FakeTaskRow(id="t2"),
    ]
    out = await task_db_read(
        client=fake_client, user_id="nick", status="backlog", domain="personal",
    )
    assert out["ok"] is True
    assert [t["id"] for t in out["tasks"]] == ["t1", "t2"]
    kwargs = fake_client.list_tasks.call_args.kwargs
    assert kwargs["user_id"] == "nick"
    assert kwargs["status"] == TaskStatus.BACKLOG
    assert kwargs["domain"] == TaskDomain.PERSONAL


@pytest.mark.asyncio
async def test_task_db_read_unknown_status_raises(fake_client):
    with pytest.raises(TaskDbReadError):
        await task_db_read(client=fake_client, status="nonsense")


@pytest.mark.asyncio
async def test_task_db_read_unknown_domain_raises(fake_client):
    with pytest.raises(TaskDbReadError):
        await task_db_read(client=fake_client, domain="nonsense")


@pytest.mark.asyncio
async def test_task_db_read_list_propagates_client_failure(fake_client):
    fake_client.list_tasks.side_effect = RuntimeError("db closed")
    with pytest.raises(TaskDbReadError):
        await task_db_read(client=fake_client, user_id="nick")


@pytest.mark.asyncio
async def test_task_db_read_never_calls_mutating_methods(fake_client):
    fake_client.create_task = AsyncMock()
    fake_client.update_task = AsyncMock()
    fake_client.transition_task_state = AsyncMock()
    fake_client.list_tasks.return_value = []
    fake_client.get_task.return_value = FakeTaskRow(id="t1")
    await task_db_read(client=fake_client, user_id="nick")
    await task_db_read(client=fake_client, task_id="t1")
    fake_client.create_task.assert_not_called()
    fake_client.update_task.assert_not_called()
    fake_client.transition_task_state.assert_not_called()
