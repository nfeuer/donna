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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from donna.models.router import ContextOverflowError
from donna.notifications.service import CHANNEL_TASKS, NOTIF_REMINDER, NotificationService
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.models.router import ModelRouter

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
        router: ModelRouter | None = None,
    ) -> None:
        self._db = db
        self._service = service
        self._user_id = user_id
        self._router = router
        # task_id → date the reminder was sent (reset daily for reschedules)
        self._sent: dict[str, str] = {}

    async def run(self) -> None:
        """Loop forever, checking for upcoming tasks every minute."""
        logger.info("reminder_scheduler_started", lead_minutes=REMINDER_LEAD_MINUTES)
        _last_flush_date: str | None = None

        while True:
            now = datetime.now(tz=UTC)
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

            content, llm_generated = await self._generate_reminder(task)

            sent = await self._service.dispatch(
                notification_type=NOTIF_REMINDER,
                content=content,
                channel=CHANNEL_TASKS,
                priority=task.priority or 2,
            )

            # Persist nudge event and increment counter.
            await self._db.increment_nudge_count(task.id)
            await self._db.record_nudge_event(
                user_id=self._user_id,
                task_id=task.id,
                nudge_type="reminder",
                channel="discord",
                message_text=content,
                llm_generated=llm_generated,
            )

            # Record as sent regardless (queued = still sent eventually).
            self._sent[task.id] = today_str
            logger.info(
                "reminder_dispatched",
                task_id=task.id,
                title=task.title,
                scheduled_start=task.scheduled_start,
                sent_immediately=sent,
                llm_generated=llm_generated,
            )


    async def _generate_reminder(self, task: TaskRow) -> tuple[str, bool]:
        """Generate a reminder via local LLM, falling back to a template.

        Returns (reminder_text, llm_generated).
        """
        duration_str = (
            f"{task.estimated_duration} min" if task.estimated_duration else "unknown"
        )
        fallback = (
            f"\u23f0 '{task.title}' starts in {REMINDER_LEAD_MINUTES} minutes."
            f" Duration: {duration_str}."
        )

        if self._router is None:
            return fallback, False

        try:
            prompt = (
                f"Task: {task.title}\n"
                f"Domain: {task.domain}\n"
                f"Priority: {task.priority}\n"
                f"Scheduled start: {task.scheduled_start}\n"
                f"Estimated duration: {duration_str}\n"
                f"Description: {task.description or 'none'}\n"
                f"This is a pre-task reminder (task starts in {REMINDER_LEAD_MINUTES} minutes).\n"
            )
            result, _meta = await self._router.complete(
                prompt=prompt,
                task_type="generate_reminder",
                task_id=task.id,
                user_id=self._user_id,
            )
            text = result.get("reminder_text", "").strip()
            if text:
                return text, True
            logger.warning("reminder_llm_empty_response", task_id=task.id)
        except ContextOverflowError:
            raise
        except Exception:
            logger.exception("reminder_llm_failed", task_id=task.id)

        return fallback, False


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO datetime string into a UTC-aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None
