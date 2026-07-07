"""Tests for the task edit pathway (domain + duration)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from donna.api.routes.tasks import UpdateTaskRequest, update_task
from donna.tasks.db_models import TaskStatus


@dataclass
class FakeRow:
    id: str = "t1"
    user_id: str = "nick"
    title: str = "Email the client"
    description: str | None = None
    domain: str = "personal"
    priority: int = 2
    status: str = "pending"
    estimated_duration: int | None = 60
    deadline: str | None = None
    deadline_type: str = "none"
    scheduled_start: str | None = None
    tags: list | None = None
    created_at: str = "2026-06-10T00:00:00"
    created_via: str | None = "app"


def _request_with_db(db) -> MagicMock:
    req = MagicMock()
    req.app.state.db = db
    return req


def test_update_request_accepts_domain_and_duration() -> None:
    body = UpdateTaskRequest(domain="work", estimated_duration=15)
    dumped = body.model_dump(exclude_none=True)
    assert dumped == {"domain": "work", "estimated_duration": 15}


def test_update_request_rejects_invalid_domain() -> None:
    with pytest.raises(ValidationError):
        UpdateTaskRequest(domain="banana")


async def test_update_task_persists_with_api_source() -> None:
    row = FakeRow()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=row)
    db.update_task = AsyncMock(return_value=FakeRow(domain="work", estimated_duration=15))

    body = UpdateTaskRequest(domain="work", estimated_duration=15)
    result = await update_task(_request_with_db(db), "t1", body, user_id="nick")

    db.update_task.assert_awaited_once()
    assert db.update_task.await_args.kwargs["source"] == "api"
    assert db.update_task.await_args.kwargs["domain"] == "work"
    assert db.update_task.await_args.kwargs["estimated_duration"] == 15
    assert result.domain == "work"


async def test_update_task_status_routes_through_state_machine() -> None:
    db = MagicMock()
    db.get_task = AsyncMock(return_value=FakeRow(status="done"))
    db.transition_task_state = AsyncMock(return_value=["set_completed_at"])
    db.update_task = AsyncMock()

    body = UpdateTaskRequest(status="done")
    result = await update_task(_request_with_db(db), "t1", body, user_id="nick")

    db.transition_task_state.assert_awaited_once_with("t1", TaskStatus.DONE)
    db.update_task.assert_not_awaited()
    assert result.status == "done"


async def test_update_task_rejects_garbage_status() -> None:
    row = FakeRow(status="backlog")
    db = MagicMock()
    db.get_task = AsyncMock(return_value=row)
    db.transition_task_state = AsyncMock()

    body = UpdateTaskRequest(status="banana")
    with pytest.raises(HTTPException) as exc_info:
        await update_task(_request_with_db(db), "t1", body, user_id="nick")

    assert exc_info.value.status_code == 422
    db.transition_task_state.assert_not_awaited()


async def test_update_task_status_and_fields_together() -> None:
    """A PATCH carrying both status and a plain field transitions AND updates."""
    db = MagicMock()
    db.get_task = AsyncMock(return_value=FakeRow(status="backlog"))
    db.transition_task_state = AsyncMock(return_value=["set_completed_at"])
    db.update_task = AsyncMock(return_value=FakeRow(status="done", domain="work"))

    body = UpdateTaskRequest(status="done", domain="work")
    result = await update_task(_request_with_db(db), "t1", body, user_id="nick")

    db.transition_task_state.assert_awaited_once_with("t1", TaskStatus.DONE)
    db.update_task.assert_awaited_once()
    assert "status" not in db.update_task.await_args.kwargs
    assert db.update_task.await_args.kwargs["domain"] == "work"
    assert result.domain == "work"


async def test_admin_update_uses_dashboard_source() -> None:
    from donna.api.routes.admin_tasks import AdminTaskUpdate, update_task_admin

    row = FakeRow()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=row)
    db.update_task = AsyncMock(return_value=FakeRow(estimated_duration=15))

    body = AdminTaskUpdate(estimated_duration=15)
    result = await update_task_admin(_request_with_db(db), "t1", body)

    assert db.update_task.await_args.kwargs["source"] == "dashboard"
    assert db.update_task.await_args.kwargs["estimated_duration"] == 15
    assert result["estimated_duration"] == 15
