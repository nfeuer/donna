"""Chat action handlers for task operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from donna.chat.types import ActionContext, ActionResult
from donna.tasks.db_models import TaskDomain, TaskStatus


async def query_tasks(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    status = None
    if params.get("status"):
        try:
            status = TaskStatus(params["status"])
        except ValueError:
            return ActionResult(success=False, error=f"Invalid status: {params['status']}")

    domain = None
    if params.get("domain"):
        try:
            domain = TaskDomain(params["domain"])
        except ValueError:
            return ActionResult(success=False, error=f"Invalid domain: {params['domain']}")

    tasks = await ctx.db.list_tasks(
        user_id=ctx.user_id, status=status, domain=domain,
    )

    task_list = [
        {"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "domain": t.domain}
        for t in tasks
    ]
    return ActionResult(
        success=True,
        data={"tasks": task_list, "count": len(task_list)},
        summary=f"Found {len(task_list)} task(s).",
    )


async def get_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    task_id = params.get("task_id")
    title_search = params.get("title_search")

    if task_id:
        task = await ctx.db.get_task(task_id)
        if task is None:
            return ActionResult(success=False, error=f"Task {task_id} not found.")
    elif title_search:
        all_tasks = await ctx.db.list_tasks(user_id=ctx.user_id)
        search_lower = title_search.lower()
        matches = [t for t in all_tasks if search_lower in t.title.lower()]
        if not matches:
            return ActionResult(success=False, error=f"No task matching '{title_search}'.")
        task = matches[0]
    else:
        if ctx.dashboard_context and ctx.dashboard_context.get("selected_item"):
            item = ctx.dashboard_context["selected_item"]
            if item.get("type") == "task":
                task = await ctx.db.get_task(item["id"])
                if task is None:
                    return ActionResult(success=False, error="Selected task not found.")
            else:
                return ActionResult(success=False, error="No task ID or search term provided.")
        else:
            return ActionResult(success=False, error="No task ID or search term provided.")

    return ActionResult(
        success=True,
        data={
            "id": task.id, "title": task.title, "description": task.description,
            "status": task.status, "priority": task.priority, "domain": task.domain,
            "notes": task.notes, "created_at": str(task.created_at),
            "scheduled_start": str(task.scheduled_start) if task.scheduled_start else None,
        },
        summary=f"Task '{task.title}' — {task.status}, priority {task.priority}.",
    )


async def create_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    title = params["title"]
    description = params.get("description")
    priority_str = params.get("priority", "P2")
    priority_map = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    priority = priority_map.get(priority_str, 2)

    domain = TaskDomain.PERSONAL
    if params.get("domain"):
        try:
            domain = TaskDomain(params["domain"])
        except ValueError:
            pass

    from donna.tasks.db_models import InputChannel
    task = await ctx.db.create_task(
        user_id=ctx.user_id,
        title=title,
        description=description,
        domain=domain,
        priority=priority,
        created_via=InputChannel.APP,
    )
    return ActionResult(
        success=True,
        data={"id": task.id, "title": task.title, "status": task.status},
        summary=f"Created task '{task.title}' (id: {task.id}).",
    )


async def update_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    task_id = params.get("task_id")
    if not task_id:
        return ActionResult(success=False, error="task_id is required.")

    updates: dict[str, Any] = {}
    if params.get("status"):
        updates["status"] = params["status"]
    if params.get("priority"):
        priority_map = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        updates["priority"] = priority_map.get(params["priority"], 2)
    if params.get("notes"):
        updates["notes"] = params["notes"]

    if not updates:
        return ActionResult(success=False, error="No fields to update.")

    task = await ctx.db.update_task(task_id, **updates)
    if task is None:
        return ActionResult(success=False, error=f"Task {task_id} not found.")

    return ActionResult(
        success=True,
        data={"id": task.id, "title": task.title, "status": task.status, "priority": task.priority},
        summary=f"Updated task '{task.title}'.",
    )


async def reschedule_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    task_id = params.get("task_id")
    scheduled_start_str = params.get("scheduled_start")

    if not task_id or not scheduled_start_str:
        return ActionResult(success=False, error="task_id and scheduled_start are required.")

    try:
        scheduled_start = datetime.fromisoformat(scheduled_start_str)
    except ValueError:
        return ActionResult(success=False, error=f"Invalid date format: {scheduled_start_str}")

    task = await ctx.db.update_task(task_id, scheduled_start=scheduled_start.isoformat())
    if task is None:
        return ActionResult(success=False, error=f"Task {task_id} not found.")

    return ActionResult(
        success=True,
        data={"id": task.id, "title": task.title, "scheduled_start": scheduled_start_str},
        summary=f"Rescheduled '{task.title}' to {scheduled_start_str}.",
    )
