"""Auto-scheduler — subscribes to task lifecycle events and schedules tasks.

On task_created: if no challenger is pending, schedule immediately.
On challenger_resolved: schedule the task after Q&A is complete.

Calendar fallback: when GoogleCalendarClient is unavailable, uses
Scheduler.find_next_slot() with an empty event list and sets
scheduled_start directly without creating a calendar event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from donna.notifications.service import CHANNEL_TASKS, NOTIF_REMINDER
from donna.scheduling.scheduler import NoSlotFoundError, Scheduler
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.integrations.calendar import GoogleCalendarClient
    from donna.notifications.service import NotificationService

logger = structlog.get_logger()


class AutoScheduler:
    """Event-driven auto-scheduler for newly created tasks."""

    def __init__(
        self,
        scheduler: Scheduler,
        db: Database,
        calendar_client: GoogleCalendarClient | None,
        calendar_id: str,
        notification_service: NotificationService | None,
    ) -> None:
        self._scheduler = scheduler
        self._db = db
        self._calendar_client = calendar_client
        self._calendar_id = calendar_id
        self._notification_service = notification_service

    async def on_task_created(self, task: TaskRow, **context: Any) -> None:
        if context.get("challenger_pending", False):
            logger.info("auto_scheduler_deferred_challenger", task_id=task.id)
            return
        await self._schedule(task)

    async def on_challenger_resolved(self, task: TaskRow, **context: Any) -> None:
        fresh = await self._db.get_task(task.id)
        if fresh is None:
            return
        await self._schedule(fresh)

    async def _schedule(self, task: TaskRow) -> None:
        if task.status != TaskStatus.BACKLOG.value:
            logger.info("auto_scheduler_skip_not_backlog", task_id=task.id, status=task.status)
            return

        slot = None
        try:
            if self._calendar_client is not None:
                slot = await self._scheduler.schedule_task(
                    task, self._db, self._calendar_client, self._calendar_id
                )
            else:
                slot = self._scheduler.find_next_slot(task, [])
                await self._db.transition_task_state(task.id, TaskStatus.SCHEDULED)
                await self._db.update_task(
                    task.id,
                    scheduled_start=slot.start,
                    donna_managed=True,
                )
                logger.info("auto_scheduler_fallback_mode", task_id=task.id)
        except NoSlotFoundError:
            logger.warning("auto_scheduler_no_slot", task_id=task.id)
            return
        except Exception as exc:
            logger.exception("auto_scheduler_failed", task_id=task.id)
            if self._notification_service is not None:
                await self._notification_service.dispatch_fallback_alert(
                    component="auto_scheduler",
                    error=f"Scheduling failed: {type(exc).__name__}: {exc}",
                    fallback="task left in backlog",
                    context={"task_id": task.id},
                )
            return

        if slot is None:
            return

        logger.info(
            "auto_scheduler_scheduled",
            task_id=task.id,
            slot_start=slot.start.isoformat(),
            slot_end=slot.end.isoformat(),
        )

        if self._notification_service is not None:
            start_fmt = slot.start.strftime("%A %-I:%M %p")
            end_fmt = slot.end.strftime("%-I:%M %p")
            await self._notification_service.dispatch(
                notification_type=NOTIF_REMINDER,
                content=f"Scheduled '{task.title}' for {start_fmt}–{end_fmt}.",
                channel=CHANNEL_TASKS,
                priority=task.priority or 2,
            )
