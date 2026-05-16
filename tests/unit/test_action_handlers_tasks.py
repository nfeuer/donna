"""Tests for task action handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from donna.chat.actions.tasks import query_tasks, get_task, create_task, update_task, reschedule_task
from donna.chat.types import ActionContext


@pytest.fixture
def ctx() -> ActionContext:
    db = AsyncMock()
    return ActionContext(
        db=db, user_id="nick", session_id="sess-1",
        config=MagicMock(), dashboard_context=None,
    )


@pytest.mark.asyncio
async def test_query_tasks_empty(ctx: ActionContext) -> None:
    ctx.db.list_tasks.return_value = []
    result = await query_tasks({}, ctx)
    assert result.success is True
    assert result.data["count"] == 0


@pytest.mark.asyncio
async def test_query_tasks_with_status_filter(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t1", title="Test", status="backlog", priority=2, domain="personal")
    ctx.db.list_tasks.return_value = [mock_task]
    result = await query_tasks({"status": "backlog"}, ctx)
    assert result.success is True
    assert result.data["count"] == 1


@pytest.mark.asyncio
async def test_query_tasks_invalid_status(ctx: ActionContext) -> None:
    result = await query_tasks({"status": "invalid_status"}, ctx)
    assert result.success is False
    assert "Invalid status" in (result.error or "")


@pytest.mark.asyncio
async def test_get_task_by_id(ctx: ActionContext) -> None:
    mock_task = MagicMock(
        id="t1", title="Fix auth", description="desc", status="in_progress",
        priority=1, domain="work", notes=None, created_at="2026-05-15",
        scheduled_start=None,
    )
    ctx.db.get_task.return_value = mock_task
    result = await get_task({"task_id": "t1"}, ctx)
    assert result.success is True
    assert result.data["title"] == "Fix auth"


@pytest.mark.asyncio
async def test_get_task_not_found(ctx: ActionContext) -> None:
    ctx.db.get_task.return_value = None
    result = await get_task({"task_id": "nonexistent"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_task(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t-new", title="New task", status="backlog")
    ctx.db.create_task.return_value = mock_task
    result = await create_task({"title": "New task"}, ctx)
    assert result.success is True
    assert result.data["id"] == "t-new"


@pytest.mark.asyncio
async def test_update_task(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t1", title="Fix auth", status="done", priority=1)
    ctx.db.update_task.return_value = mock_task
    result = await update_task({"task_id": "t1", "status": "done"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_update_task_no_fields(ctx: ActionContext) -> None:
    result = await update_task({"task_id": "t1"}, ctx)
    assert result.success is False
    assert "No fields" in (result.error or "")


@pytest.mark.asyncio
async def test_reschedule_task(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t1", title="Review", scheduled_start="2026-05-20")
    ctx.db.update_task.return_value = mock_task
    result = await reschedule_task({"task_id": "t1", "scheduled_start": "2026-05-20"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_reschedule_task_invalid_date(ctx: ActionContext) -> None:
    result = await reschedule_task({"task_id": "t1", "scheduled_start": "not-a-date"}, ctx)
    assert result.success is False
    assert "Invalid date" in (result.error or "")
