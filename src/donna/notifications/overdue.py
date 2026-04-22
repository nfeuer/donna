"""Overdue task detection — nudge the user when tasks run past their end time.

Background async task that runs every 15 minutes. For every scheduled or
in-progress task whose estimated end time + 30-minute buffer has passed,
it creates a Discord thread in #donna-tasks with an overdue nudge.

User replies in the thread:
  "done"       → transitions task to in_progress → done (sets completed_at)
  "reschedule" → transitions task to in_progress → scheduled, finds next slot

Respects blackout/quiet hours via NotificationService — nudges are queued
during blackout (12 AM–6 AM) and replayed at 6 AM.

See slices/slice_05_reminders_digest.md and docs/notifications.md.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from donna.integrations.discord_bot import DonnaBot
from donna.models.router import ContextOverflowError
from donna.notifications.service import CHANNEL_TASKS, NOTIF_OVERDUE, NotificationService
from donna.scheduling.scheduler import Scheduler
from donna.tasks.database import Database
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.models.router import ModelRouter
    from donna.notifications.escalation import EscalationManager

logger = structlog.get_logger()

OVERDUE_BUFFER_MINUTES = 30
CHECK_INTERVAL_SECONDS = 900  # 15 minutes


class OverdueDetector:
    """Detects overdue tasks and sends nudges via Discord threads.

    Usage:
        detector = OverdueDetector(db, service, bot, scheduler, client, calendar_id, user_id)
        # Register the reply handler before starting the bot loop:
        bot = DonnaBot(..., overdue_reply_handler=detector.handle_reply)
        asyncio.create_task(detector.run())
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        bot: DonnaBot,
        scheduler: Scheduler,
        calendar_id: str,
        user_id: str,
        escalation_manager: EscalationManager | None = None,
        router: ModelRouter | None = None,
    ) -> None:
        self._db = db
        self._service = service
        self._bot = bot
        self._scheduler = scheduler
        self._calendar_id = calendar_id
        self._user_id = user_id
        self._escalation_manager = escalation_manager
        self._router = router
        # task_id set: only nudge once per day (reset at midnight).
        self._nudged: set[str] = set()
        self._nudged_date: str = ""

    async def run(self) -> None:
        """Loop forever, checking for overdue tasks every 15 minutes."""
        logger.info(
            "overdue_detector_started",
            buffer_minutes=OVERDUE_BUFFER_MINUTES,
            interval_seconds=CHECK_INTERVAL_SECONDS,
        )

        while True:
            now = datetime.now(tz=UTC)
            today_str = now.date().isoformat()

            # Reset nudge set daily.
            if self._nudged_date != today_str:
                self._nudged.clear()
                self._nudged_date = today_str

            try:
                await self._check_and_nudge(now)
            except Exception:
                logger.exception("overdue_check_failed")

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _check_and_nudge(self, now: datetime) -> None:
        """Query scheduled/in-progress tasks and send nudges for overdue ones."""
        for status in (TaskStatus.SCHEDULED, TaskStatus.IN_PROGRESS):
            tasks = await self._db.list_tasks(user_id=self._user_id, status=status)
            for task in tasks:
                if task.id in self._nudged:
                    continue
                if not task.scheduled_start:
                    continue

                start = _parse_dt(task.scheduled_start)
                if start is None:
                    continue

                duration_min = task.estimated_duration or 0
                overdue_at = start + timedelta(minutes=duration_min + OVERDUE_BUFFER_MINUTES)

                if now <= overdue_at:
                    continue

                overdue_minutes = int((now - overdue_at).total_seconds() / 60)
                nudge_text, llm_generated = await self._generate_nudge(
                    task=task,
                    now=now,
                    overdue_minutes=overdue_minutes,
                )

                if self._escalation_manager is not None:
                    # Route through escalation tiers (Discord → SMS → ...).
                    await self._escalation_manager.escalate(
                        task_id=task.id,
                        task_title=task.title,
                        nudge_text=nudge_text,
                        priority=task.priority or 2,
                    )
                    sent = True
                else:
                    # Fallback: Discord-only (no escalation configured).
                    sent = await self._service.dispatch(
                        notification_type=NOTIF_OVERDUE,
                        content=nudge_text,
                        channel=CHANNEL_TASKS,
                        priority=task.priority or 2,
                    )

                if sent:
                    # Create thread so user can reply in context.
                    await self._bot.create_overdue_thread(
                        task_id=task.id,
                        task_title=task.title,
                        nudge_text=nudge_text,
                    )

                self._nudged.add(task.id)

                # Persist nudge event and increment counter.
                await self._db.increment_nudge_count(task.id)
                await self._db.record_nudge_event(
                    user_id=self._user_id,
                    task_id=task.id,
                    nudge_type="overdue",
                    channel="discord",
                    message_text=nudge_text,
                    llm_generated=llm_generated,
                )

                logger.info(
                    "overdue_nudge_dispatched",
                    task_id=task.id,
                    title=task.title,
                    overdue_at=overdue_at.isoformat(),
                    sent_immediately=sent,
                    llm_generated=llm_generated,
                )

    async def _generate_nudge(
        self,
        task: object,
        now: datetime,
        overdue_minutes: int,
    ) -> tuple[str, bool]:
        """Generate a nudge message via local LLM, falling back to a template.

        Returns (nudge_text, llm_generated).
        """
        time_str = now.strftime("%I:%M %p")
        fallback = (
            f"It's {time_str} and you haven't touched '{getattr(task, 'title', '')}'."
            " Did you finish it or should I find time tomorrow?"
        )

        if self._router is None:
            return fallback, False

        try:
            prompt = (
                f"Task: {getattr(task, 'title', '')}\n"
                f"Domain: {getattr(task, 'domain', 'personal')}\n"
                f"Priority: {getattr(task, 'priority', 2)}\n"
                f"Scheduled start: {getattr(task, 'scheduled_start', 'unknown')}\n"
                f"Time overdue: {overdue_minutes} minutes\n"
                f"Nudge count: {getattr(task, 'nudge_count', 0)}\n"
                f"Reschedule count: {getattr(task, 'reschedule_count', 0)}\n"
                f"Current time: {time_str}\n"
            )
            result, _meta = await self._router.complete(
                prompt=prompt,
                task_type="generate_nudge",
                task_id=getattr(task, "id", None),
                user_id=self._user_id,
            )
            nudge_text = result.get("nudge_text", "").strip()
            if nudge_text:
                return nudge_text, True
            logger.warning("nudge_llm_empty_response", task_id=getattr(task, "id", None))
        except ContextOverflowError:
            raise
        except Exception:
            logger.exception("nudge_llm_failed", task_id=getattr(task, "id", None))

        return fallback, False

    async def handle_reply(self, task_id: str, reply: str) -> None:
        """Handle user reply in an overdue thread.

        Args:
            task_id: The task that was nudged.
            reply: Normalised (lower-case stripped) user reply text.
        """
        task = await self._db.get_task(task_id)
        if task is None:
            logger.warning("overdue_reply_task_not_found", task_id=task_id)
            return

        if reply.startswith("done"):
            if self._escalation_manager is not None:
                await self._escalation_manager.acknowledge(task_id)
            await self._mark_done(task_id, task)
        elif reply.startswith("reschedule"):
            if self._escalation_manager is not None:
                await self._escalation_manager.acknowledge(task_id)
            await self._reschedule(task_id, task)
        elif reply.startswith("busy"):
            if self._escalation_manager is not None:
                await self._escalation_manager.backoff(task_id)
            logger.info("overdue_reply_busy", task_id=task_id)
        else:
            logger.info("overdue_reply_unrecognised", task_id=task_id, reply=reply[:50])

    async def _mark_done(self, task_id: str, task: object) -> None:
        """Transition task → in_progress → done and set completed_at."""
        current_status = getattr(task, "status", "")
        if current_status != TaskStatus.IN_PROGRESS.value:
            try:
                await self._db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
            except Exception:
                logger.exception("overdue_mark_done_transition_to_in_progress_failed", task_id=task_id)
                return

        try:
            await self._db.transition_task_state(task_id, TaskStatus.DONE)
            await self._db.update_task(task_id, completed_at=datetime.now(UTC))
            logger.info("overdue_task_marked_done", task_id=task_id)
        except Exception:
            logger.exception("overdue_mark_done_failed", task_id=task_id)

    async def _reschedule(self, task_id: str, task: object) -> None:
        """Transition task → in_progress → scheduled and find next slot."""
        from donna.integrations.calendar import GoogleCalendarClient

        current_status = getattr(task, "status", "")
        if current_status != TaskStatus.IN_PROGRESS.value:
            try:
                await self._db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
            except Exception:
                logger.exception("overdue_reschedule_transition_in_progress_failed", task_id=task_id)
                return

        try:
            # Transition to scheduled before scheduler assigns a new slot.
            await self._db.transition_task_state(task_id, TaskStatus.SCHEDULED)
        except Exception:
            logger.exception("overdue_reschedule_transition_scheduled_failed", task_id=task_id)
            return

        try:
            refreshed = await self._db.get_task(task_id)
            if refreshed is None:
                return
            # Use the calendar client from the scheduler's perspective.
            # The client attribute is set during wiring in server.py.
            client: GoogleCalendarClient | None = getattr(self._scheduler, "_client", None)
            if client is None:
                logger.warning("overdue_reschedule_no_calendar_client", task_id=task_id)
                return
            await self._scheduler.schedule_task(
                task=refreshed,
                db=self._db,
                client=client,
                calendar_id=self._calendar_id,
                force_reschedule=True,
            )
            logger.info("overdue_task_rescheduled", task_id=task_id)
        except Exception:
            logger.exception("overdue_reschedule_schedule_failed", task_id=task_id)


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
