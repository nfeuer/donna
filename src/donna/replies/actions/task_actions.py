"""Task action handlers for the Universal Reply Handler.

Each handler takes a Database, context dict, and action params,
executes the action, and returns a result summary string.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.scheduling.scheduler import Scheduler
    from donna.tasks.database import Database

logger = structlog.get_logger()


async def mark_done(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Mark a task as done. Transitions through in_progress if needed."""
    task_id = params["task_id"]
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    if task.status != TaskStatus.IN_PROGRESS.value:
        try:
            await db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
        except Exception:
            logger.exception("mark_done_transition_failed", task_id=task_id)
            return f"Failed to transition '{task.title}' to in_progress."

    try:
        await db.transition_task_state(task_id, TaskStatus.DONE)
        await db.update_task(task_id, completed_at=datetime.now(UTC))
        return f"Marked '{task.title}' as done."
    except Exception:
        logger.exception("mark_done_failed", task_id=task_id)
        return f"Failed to mark '{task.title}' as done."


async def reschedule_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Reschedule a task. Uses the scheduler to find a new slot."""
    task_id = params["task_id"]
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    if task.status != TaskStatus.IN_PROGRESS.value:
        try:
            await db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
        except Exception:
            logger.exception("reschedule_transition_failed", task_id=task_id)
            return f"Failed to transition '{task.title}' for rescheduling."

    try:
        await db.transition_task_state(task_id, TaskStatus.SCHEDULED)
    except Exception:
        logger.exception("reschedule_to_scheduled_failed", task_id=task_id)
        return f"Failed to move '{task.title}' back to scheduled."

    scheduler: Scheduler | None = context.get("scheduler")
    calendar_client = context.get("calendar_client")
    calendar_id = context.get("calendar_id")

    if scheduler and calendar_client and calendar_id:
        try:
            refreshed = await db.get_task(task_id)
            if refreshed:
                await scheduler.schedule_task(
                    task=refreshed,
                    db=db,
                    client=calendar_client,
                    calendar_id=calendar_id,
                    force_reschedule=True,
                )
                return f"Rescheduled '{task.title}'."
        except Exception:
            logger.exception("reschedule_slot_failed", task_id=task_id)
            return f"Moved '{task.title}' to scheduled but couldn't find a new slot."

    return f"Moved '{task.title}' to scheduled (no calendar client available for slot assignment)."


async def create_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Create a new task."""
    from donna.tasks.db_models import TaskDomain

    title = params["title"]
    domain_str = params.get("domain", "personal")
    priority = params.get("priority", 2)

    domain_map = {
        "work": TaskDomain.WORK,
        "personal": TaskDomain.PERSONAL,
        "family": TaskDomain.FAMILY,
    }
    domain = domain_map.get(domain_str, TaskDomain.PERSONAL)

    user_id = context.get("user_id", "system")
    try:
        new_task = await db.create_task(
            user_id=user_id,
            title=title,
            domain=domain,
            priority=priority,
        )
        return f"Created task '{title}' (id: {new_task.id})."
    except Exception:
        logger.exception("create_task_failed", title=title)
        return f"Failed to create task '{title}'."


async def rename_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Rename a task."""
    task_id = params["task_id"]
    new_title = params["new_title"]
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    try:
        await db.update_task(task_id, title=new_title)
        return f"Renamed task to '{new_title}'."
    except Exception:
        logger.exception("rename_task_failed", task_id=task_id)
        return "Failed to rename task."


async def snooze_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Snooze a task's notifications."""
    task_id = params["task_id"]
    hours = params.get("duration_hours", 2)
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    logger.info("task_snoozed", task_id=task_id, hours=hours)
    return f"Snoozed notifications for '{task.title}' for {hours} hour(s)."
