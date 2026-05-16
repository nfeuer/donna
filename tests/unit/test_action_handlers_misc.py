"""Tests for skills, automations, and debug action handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.actions.automations import create_automation, list_automations
from donna.chat.actions.debug import get_agent_status, get_debug_data
from donna.chat.actions.skills import create_skill_draft, execute_skill, list_skills
from donna.chat.types import ActionContext


@pytest.fixture
def ctx() -> ActionContext:
    db = AsyncMock()
    return ActionContext(
        db=db, user_id="nick", session_id="sess-1",
        config=MagicMock(), dashboard_context=None,
    )


@pytest.mark.asyncio
async def test_execute_skill_missing_name(ctx: ActionContext) -> None:
    result = await execute_skill({}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_execute_skill_queued(ctx: ActionContext) -> None:
    ctx.db.get_skill = AsyncMock(return_value=MagicMock())
    ctx.db.queue_skill_run = AsyncMock(return_value="run-123")
    result = await execute_skill({"skill_name": "product_watch"}, ctx)
    assert result.success is True
    assert result.data["status"] == "queued"


@pytest.mark.asyncio
async def test_list_skills(ctx: ActionContext) -> None:
    ctx.db.list_skills = AsyncMock(return_value=[
        MagicMock(name="product_watch", status="active"),
        MagicMock(name="email_triage", status="active"),
    ])
    result = await list_skills({}, ctx)
    assert result.success is True
    assert result.data["count"] == 2


@pytest.mark.asyncio
async def test_create_skill_draft_missing_fields(ctx: ActionContext) -> None:
    result = await create_skill_draft({"name": "test"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_automation_missing_fields(ctx: ActionContext) -> None:
    result = await create_automation({"name": "test"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_automation_success(ctx: ActionContext) -> None:
    ctx.db.create_automation = AsyncMock(return_value="auto-123")
    result = await create_automation(
        {"name": "Daily watch", "trigger": "schedule", "skill_name": "product_watch"},
        ctx,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_list_automations(ctx: ActionContext) -> None:
    ctx.db.list_automations = AsyncMock(return_value=[
        MagicMock(
            name="Daily watch", trigger_type="schedule",
            active=True, skill_name="product_watch",
        ),
    ])
    result = await list_automations({}, ctx)
    assert result.success is True
    assert result.data["count"] == 1


@pytest.mark.asyncio
async def test_get_debug_data(ctx: ActionContext) -> None:
    ctx.db.list_tasks = AsyncMock(return_value=[
        MagicMock(status="in_progress"),
        MagicMock(status="done"),
        MagicMock(status="done"),
    ])
    result = await get_debug_data({}, ctx)
    assert result.success is True
    assert result.data["total_tasks"] == 3


@pytest.mark.asyncio
async def test_get_agent_status(ctx: ActionContext) -> None:
    del ctx.db.list_agent_runs
    ctx.db.list_skill_runs = AsyncMock(return_value=[
        MagicMock(id="r1", status="complete", started_at="2026-05-15", skill_name="product_watch"),
    ])
    result = await get_agent_status({"agent_name": "product_watch"}, ctx)
    assert result.success is True
    assert result.data["count"] == 1
