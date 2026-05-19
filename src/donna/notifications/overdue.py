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
import zoneinfo
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from donna.integrations.discord_bot import DonnaBot
from donna.models.router import ContextOverflowError
from donna.notifications.service import NotificationService
from donna.scheduling.scheduler import Scheduler
from donna.tasks.database import Database
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.models.router import ModelRouter
    from donna.notifications.escalation import EscalationManager
    from donna.replies.handler import ReplyHandler

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
        reply_handler: ReplyHandler | None = None,
        calendar_client: Any | None = None,
        tz: zoneinfo.ZoneInfo | None = None,
    ) -> None:
        self._db = db
        self._service = service
        self._bot = bot
        self._scheduler = scheduler
        self._calendar_id = calendar_id
        self._user_id = user_id
        self._escalation_manager = escalation_manager
        self._router = router
        self._reply_handler = reply_handler
        self._calendar_client = calendar_client
        self._tz = tz
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
            except Exception as exc:
                logger.exception("overdue_check_failed")
                await self._service.dispatch_fallback_alert(
                    component="overdue_detector",
                    error=f"Overdue check failed: {type(exc).__name__}: {exc}",
                    fallback="skipped this check cycle",
                )

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

                # Send nudge as a threaded message so the user can reply.
                # create_overdue_thread sends the message AND creates the thread.
                await self._bot.create_overdue_thread(
                    task_id=task.id,
                    task_title=task.title,
                    nudge_text=nudge_text,
                )
                sent = True

                if self._escalation_manager is not None:
                    # Register with escalation so it can advance to SMS/email
                    # if the user doesn't respond. skip_initial_dispatch=True
                    # avoids a duplicate Discord message.
                    await self._escalation_manager.escalate(
                        task_id=task.id,
                        task_title=task.title,
                        nudge_text=nudge_text,
                        priority=task.priority or 2,
                        skip_initial_dispatch=True,
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
        local_now = now.astimezone(self._tz) if self._tz else now
        time_str = local_now.strftime("%I:%M %p")
        fallback = (
            f"It's {time_str} and you haven't touched '{getattr(task, 'title', '')}'."
            " Did you finish it or should I find time tomorrow?"
        )

        if self._router is None:
            return fallback, False

        try:
            from jinja2 import Template

            template_str = self._router.get_prompt_template("generate_nudge")
            prompt = Template(template_str).render(
                task_title=getattr(task, "title", ""),
                domain=getattr(task, "domain", "personal"),
                priority=getattr(task, "priority", 2),
                scheduled_start=getattr(task, "scheduled_start", "unknown"),
                overdue_duration=overdue_minutes,
                nudge_count=getattr(task, "nudge_count", 0),
                reschedule_count=getattr(task, "reschedule_count", 0),
                current_time=time_str,
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
        except Exception as exc:
            logger.exception("nudge_llm_failed", task_id=getattr(task, "id", None))
            await self._service.dispatch_fallback_alert(
                component="overdue_nudge",
                error=(
                    f"LLM nudge failed for '{getattr(task, 'title', '?')}': "
                    f"{type(exc).__name__}: {exc}"
                ),
                fallback="template string nudge",
                context={"task_id": getattr(task, "id", None)},
            )

        return fallback, False

    async def handle_reply(self, task_id: str, reply: str) -> Any:
        """Handle user reply in an overdue thread.

        Delegates to ReplyHandler if wired, falls back to legacy keywords.
        """
        task = await self._db.get_task(task_id)
        if task is None:
            logger.warning("overdue_reply_task_not_found", task_id=task_id)
            return None

        if self._reply_handler is not None:
            thread_id = f"overdue-{task_id}"
            try:
                result = await self._reply_handler.handle(
                    thread_id, reply, task, "overdue",
                )
            except Exception as exc:
                logger.exception("reply_handler_failed", task_id=task_id)
                await self._service.dispatch_fallback_alert(
                    component="overdue_reply",
                    error=(
                        f"Reply handler crashed for task '{task.title}': "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    fallback="reply ignored",
                    context={"task_id": task_id},
                )
                return None
            logger.info(
                "overdue_reply_handled",
                task_id=task_id,
                path=result.path,
                action=result.action,
            )
            if result.path in ("fast", "plan_confirmed") and self._escalation_manager is not None:
                if result.action in ("mark_done", "reschedule"):
                    await self._escalation_manager.acknowledge(task_id)
                elif result.action == "snooze":
                    await self._escalation_manager.backoff(task_id)
            return result

        # Legacy fallback (kept until ReplyHandler is fully wired)
        done_kw = {"done", "finished", "complete", "completed", "did it", "yes"}
        reschedule_kw = {"reschedule", "tomorrow", "later", "push", "move"}
        busy_kw = {"busy", "not now", "snooze"}

        words = reply.lower()
        if any(kw in words for kw in done_kw):
            if self._escalation_manager is not None:
                await self._escalation_manager.acknowledge(task_id)
            await self._mark_done(task_id, task)
        elif any(kw in words for kw in reschedule_kw):
            if self._escalation_manager is not None:
                await self._escalation_manager.acknowledge(task_id)
            await self._reschedule(task_id, task)
        elif any(kw in words for kw in busy_kw):
            if self._escalation_manager is not None:
                await self._escalation_manager.backoff(task_id)
            logger.info("overdue_reply_busy", task_id=task_id)
        else:
            logger.info("overdue_reply_unrecognised", task_id=task_id, reply=reply[:50])
        return None

    async def _mark_done(self, task_id: str, task: object) -> None:
        """Transition task → in_progress → done and set completed_at."""
        current_status = getattr(task, "status", "")
        if current_status != TaskStatus.IN_PROGRESS.value:
            try:
                await self._db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
            except Exception:
                logger.exception(
                    "overdue_mark_done_transition_to_in_progress_failed", task_id=task_id,
                )
                return

        try:
            await self._db.transition_task_state(task_id, TaskStatus.DONE)
            await self._db.update_task(task_id, completed_at=datetime.now(UTC))
            logger.info("overdue_task_marked_done", task_id=task_id)
        except Exception:
            logger.exception("overdue_mark_done_failed", task_id=task_id)

    async def _reschedule(self, task_id: str, task: object) -> None:
        """Transition task → in_progress → scheduled and find next slot."""
        current_status = getattr(task, "status", "")
        if current_status != TaskStatus.IN_PROGRESS.value:
            try:
                await self._db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
            except Exception:
                logger.exception(
                    "overdue_reschedule_transition_in_progress_failed", task_id=task_id,
                )
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
            if self._calendar_client is not None:
                await self._scheduler.schedule_task(
                    task=refreshed,
                    db=self._db,
                    client=self._calendar_client,
                    calendar_id=self._calendar_id,
                    force_reschedule=True,
                )
                logger.info("overdue_task_rescheduled", task_id=task_id)
            else:
                # No calendar client — bump scheduled_start by 1 day as fallback.
                old_start = _parse_dt(getattr(refreshed, "scheduled_start", None))
                new_start = (old_start or datetime.now(UTC)) + timedelta(days=1)
                await self._db.update_task(
                    task_id, scheduled_start=new_start.isoformat(),
                )
                logger.info(
                    "overdue_task_rescheduled_fallback",
                    task_id=task_id,
                    new_start=new_start.isoformat(),
                )
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
