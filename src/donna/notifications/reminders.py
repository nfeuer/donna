"""Reminder scheduler — proactive 15-minute pre-task notifications.

Background async task that runs every minute. For every scheduled task
whose start time is within the next 15 minutes, it fires a reminder to
Discord #donna-tasks via the NotificationService.

Reminders are deduplicated per run: a task_id is only nudged once per
reminder window. The sent set resets on a new day so a rescheduled task
can receive a fresh reminder.

Respects blackout/quiet hours via NotificationService. At the top of each
loop, the blackout queue is flushed if the current time has crossed 6 AM.

See slices/slice_05_reminders_digest.md and docs/notifications.md.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from donna.notifications.service import CHANNEL_TASKS, NOTIF_REMINDER, NotificationService
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskStatus

logger = structlog.get_logger()

REMINDER_LEAD_MINUTES = 15
CHECK_INTERVAL_SECONDS = 60


class ReminderScheduler:
    """Checks scheduled tasks every minute and fires 15-minute reminders.

    Usage:
        scheduler = ReminderScheduler(db, service, user_id="123")
        asyncio.create_task(scheduler.run())
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        user_id: str,
    ) -> None:
        self._db = db
        self._service = service
        self._user_id = user_id
        # task_id → date the reminder was sent (reset daily for reschedules)
        self._sent: dict[str, str] = {}

    async def run(self) -> None:
        """Loop forever, checking for upcoming tasks every minute."""
        logger.info("reminder_scheduler_started", lead_minutes=REMINDER_LEAD_MINUTES)
        _last_flush_date: str | None = None

        while True:
            now = datetime.now(tz=timezone.utc)
            today_str = now.date().isoformat()

            # Flush blackout queue at boundary (6 AM).
            blackout_end = self._service._tw.blackout.end_hour
            if now.hour >= blackout_end and _last_flush_date != today_str:
                flushed = await self._service.flush_queue()
                _last_flush_date = today_str
                if flushed:
                    logger.info("reminder_scheduler_flushed_queue", count=flushed)

            try:
                await self._check_and_send(now)
            except Exception:
                logger.exception("reminder_check_failed")

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _check_and_send(self, now: datetime) -> None:
        """Query scheduled tasks and send reminders for upcoming ones."""
        tasks = await self._db.list_tasks(
            user_id=self._user_id,
            status=TaskStatus.SCHEDULED,
        )

        today_str = now.date().isoformat()
        window_end = now + timedelta(minutes=REMINDER_LEAD_MINUTES)

        for task in tasks:
            if not task.scheduled_start:
                continue

            start = _parse_dt(task.scheduled_start)
            if start is None:
                continue

            # Within the 15-minute window and still in the future.
            if not (now < start <= window_end):
                continue

            # Deduplicate: skip if we already sent a reminder today.
            sent_date = self._sent.get(task.id)
            if sent_date == today_str:
                continue

            duration_str = (
                f"{task.estimated_duration} min" if task.estimated_duration else "unknown"
            )
            content = (
                f"\u23f0 '{task.title}' starts in {REMINDER_LEAD_MINUTES} minutes."
                f" Duration: {duration_str}."
            )

            sent = await self._service.dispatch(
                notification_type=NOTIF_REMINDER,
                content=content,
                channel=CHANNEL_TASKS,
                priority=task.priority or 2,
            )

            # Record as sent regardless (queued = still sent eventually).
            self._sent[task.id] = today_str
            logger.info(
                "reminder_dispatched",
                task_id=task.id,
                title=task.title,
                scheduled_start=task.scheduled_start,
                sent_immediately=sent,
            )


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO datetime string into a UTC-aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
