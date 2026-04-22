"""Task CRUD endpoints — all require authentication.

All queries are scoped to the authenticated user_id so no user can see or
modify another user's tasks.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from donna.api.auth import CurrentUser, user_router
from donna.tasks.db_models import DeadlineType, InputChannel, TaskDomain, TaskStatus

router = user_router()


class TaskResponse(BaseModel):
    """Public task projection returned by the API."""

    id: str
    user_id: str
    title: str
    description: str | None
    domain: str
    priority: int
    status: str
    estimated_duration: int | None
    deadline: str | None
    deadline_type: str
    scheduled_start: str | None
    created_at: str
    created_via: str
    tags: list[str] | None = None

    @classmethod
    def from_row(cls, row: Any) -> TaskResponse:
        tags: list[str] | None = None
        if row.tags:
            try:
                tags = json.loads(row.tags)
            except (ValueError, TypeError):
                tags = None
        return cls(
            id=row.id,
            user_id=row.user_id,
            title=row.title,
            description=row.description,
            domain=row.domain,
            priority=row.priority,
            status=row.status,
            estimated_duration=row.estimated_duration,
            deadline=row.deadline,
            deadline_type=row.deadline_type,
            scheduled_start=row.scheduled_start,
            created_at=row.created_at,
            created_via=row.created_via,
            tags=tags,
        )


class CreateTaskRequest(BaseModel):
    title: str
    description: str | None = None
    domain: str = "personal"
    priority: int = 2
    estimated_duration: int | None = None
    deadline: str | None = None


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    status: str | None = None


@router.get("")
async def list_tasks(
    request: Request,
    user_id: CurrentUser,
    status: str | None = None,
    domain: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List tasks for the authenticated user, with optional filters."""
    db = request.app.state.db

    status_filter = TaskStatus(status) if status else None
    domain_filter = TaskDomain(domain) if domain else None

    rows = await db.list_tasks(user_id=user_id, status=status_filter, domain=domain_filter)
    page = rows[offset : offset + limit]

    return {
        "tasks": [TaskResponse.from_row(r) for r in page],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    request: Request,
    body: CreateTaskRequest,
    user_id: CurrentUser,
) -> TaskResponse:
    """Create a new task for the authenticated user."""
    db = request.app.state.db

    deadline_dt = datetime.fromisoformat(body.deadline) if body.deadline else None

    row = await db.create_task(
        user_id=user_id,
        title=body.title,
        description=body.description,
        domain=TaskDomain(body.domain),
        priority=body.priority,
        estimated_duration=body.estimated_duration,
        deadline=deadline_dt,
        deadline_type=DeadlineType.SOFT if deadline_dt else DeadlineType.NONE,
        created_via=InputChannel.APP,
    )
    return TaskResponse.from_row(row)


@router.get("/{task_id}")
async def get_task(
    request: Request,
    task_id: str,
    user_id: CurrentUser,
) -> TaskResponse:
    """Get a single task by ID. Returns 404 if the task doesn't belong to this user."""
    db = request.app.state.db
    row = await db.get_task(task_id)

    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse.from_row(row)


@router.patch("/{task_id}")
async def update_task(
    request: Request,
    task_id: str,
    body: UpdateTaskRequest,
    user_id: CurrentUser,
) -> TaskResponse:
    """Update mutable fields on a task. Returns 404 if not owned by this user."""
    db = request.app.state.db
    existing = await db.get_task(task_id)

    if existing is None or existing.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return TaskResponse.from_row(existing)

    row = await db.update_task(task_id, **updates)
    return TaskResponse.from_row(row)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(
    request: Request,
    task_id: str,
    user_id: CurrentUser,
) -> None:
    """Cancel a task (sets status → cancelled). Returns 404 if not owned by this user."""
    db = request.app.state.db
    existing = await db.get_task(task_id)

    if existing is None or existing.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    await db.update_task(task_id, status=TaskStatus.CANCELLED.value)
