"""Schedule endpoints — returns upcoming scheduled tasks for the Flutter calendar view."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request

from donna.api.auth import CurrentUser, user_router
from donna.tasks.db_models import TaskStatus

router = user_router()


def _scheduled_task_to_dict(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "scheduled_start": task.scheduled_start,
        "estimated_duration": task.estimated_duration,
        "priority": task.priority,
        "domain": task.domain,
        "donna_managed": task.donna_managed,
    }


@router.get("")
async def get_schedule(
    request: Request,
    user_id: CurrentUser,
    days: int = 7,
) -> dict[str, Any]:
    """Return scheduled tasks in the next N days for the authenticated user.

    Only tasks with status=scheduled and a scheduled_start within the window
    are included. Results are sorted by scheduled_start ascending.
    """
    db = request.app.state.db
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=max(1, min(days, 90)))

    all_scheduled = await db.list_tasks(user_id=user_id, status=TaskStatus.SCHEDULED)

    window: list[dict[str, Any]] = []
    for task in all_scheduled:
        if not task.scheduled_start:
            continue
        try:
            start = datetime.fromisoformat(task.scheduled_start)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if now <= start <= cutoff:
                window.append(_scheduled_task_to_dict(task))
        except ValueError:
            continue

    window.sort(key=lambda t: t["scheduled_start"])

    return {
        "user_id": user_id,
        "from": now.isoformat(),
        "to": cutoff.isoformat(),
        "scheduled": window,
        "count": len(window),
    }


@router.get("/week")
async def get_weekly_plan(
    request: Request,
    user_id: CurrentUser,
) -> dict[str, Any]:
    """Return the 7-day schedule for the authenticated user.

    Convenience alias for GET /schedule?days=7.
    """
    return await get_schedule(request, user_id, days=7)
