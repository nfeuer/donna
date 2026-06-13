"""Auto-scheduler — subscribes to task lifecycle events and schedules tasks.

On task_created: if no challenger is pending, schedule immediately.
On challenger_resolved: schedule the task after Q&A is complete.

Calendar fallback: when GoogleCalendarClient is unavailable, uses
Scheduler.find_next_slot() with an empty event list and sets
scheduled_start directly without creating a calendar event.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

from donna.notifications.service import (
    CHANNEL_TASKS,
    NOTIF_REMINDER,
    NOTIF_RESCHEDULE,
)
from donna.scheduling.scheduler import (
    NEGOTIATION_IMPOSSIBLE,
    NEGOTIATION_PROPOSED,
    CalendarReadError,
    NoSlotFoundError,
    Scheduler,
)
from donna.scheduling.time_intent import (
    TimeIntent,
    derive_deadline,
    derive_deadline_type,
)
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.config import NegotiationConfig
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
        from donna.scheduling.routing_gate import Route, route
        from donna.scheduling.time_intent import TimeIntent

        ti = TimeIntent.from_json(getattr(task, "time_intent_json", None))
        # Back-compat: a task may carry a bare deadline without a time_intent
        # (older rows, app-created tasks, or an LLM that emitted only `deadline`).
        # Treat any concrete deadline as a time-bound intent so it schedules
        # immediately rather than stranding in backlog.
        if ti.kind == "none" and task.deadline:
            from datetime import datetime as _dt

            try:
                due = _dt.fromisoformat(task.deadline)
            except (TypeError, ValueError):
                due = None
            if due is not None:
                strictness: Literal["hard", "soft"] = (
                    "hard" if task.deadline_type == "hard" else "soft"
                )
                ti = TimeIntent(kind="exact", due_at=due, strictness=strictness)
        decision = route(ti, priority=task.priority or 2)

        if decision.route is Route.SCHEDULER:
            # Time-bound: ALWAYS schedule now, regardless of the Challenger.
            # This is the strand-bug fix.
            await self._schedule(task)
            return

        if decision.route is Route.AUTOMATION:
            # Recurring intents are owned by the automation/cron pipeline.
            logger.info("auto_scheduler_skip_recurring", task_id=task.id)
            return

        # Route.BACKLOG: no time pressure. Leave it in backlog for the weekly
        # planner / Challenger to surface — do NOT auto-place an undated task.
        logger.info("auto_scheduler_backlog_no_time", task_id=task.id)

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
            # No slot before the deadline. Transition to needs_scheduling FIRST
            # so every downstream path is crash-consistent (design §1.8), then
            # attempt the negotiation loop (Plan 2, Slice A) when the gate passes.
            logger.warning("auto_scheduler_no_slot", task_id=task.id)
            await self._db.transition_task_state(task.id, TaskStatus.NEEDS_SCHEDULING)
            await self._maybe_negotiate(task)
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

    # ------------------------------------------------------------------
    # Negotiation hook (design §1.8 — Slice A)
    # ------------------------------------------------------------------

    def _task_time_intent(self, task: TaskRow) -> TimeIntent:
        """Resolve the task's TimeIntent, falling back to a bare deadline.

        Mirrors :meth:`Scheduler._task_time_intent` so the gate and the
        slot-finder agree on what "when" a task carries.
        """
        ti = TimeIntent.from_json(getattr(task, "time_intent_json", None))
        if ti.kind == "none" and task.deadline:
            try:
                due = datetime.fromisoformat(str(task.deadline).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                due = None
            if due is not None:
                strictness: Literal["hard", "soft"] = (
                    "hard" if task.deadline_type == "hard" else "soft"
                )
                ti = TimeIntent(kind="exact", due_at=due, strictness=strictness)
        return ti

    def _negotiation_gate(self, task: TaskRow, cfg: NegotiationConfig) -> bool:
        """Return True iff negotiation should be attempted for *task* (§1.2).

        ALL must hold: the failure was deadline-clamped (a derived deadline
        exists inside the horizon — not bare horizon exhaustion); the deadline
        is *hard* (soft tasks wait — displacing committed items for a soft
        preference violates Minimize Rescheduling); negotiation is enabled; and
        ``task.priority >= min_displacer_priority``.
        """
        if not cfg.enabled:
            return False
        ti = self._task_time_intent(task)
        deadline = derive_deadline(ti)
        if deadline is None:
            # Bare horizon exhaustion (no deadline) — not a negotiation case.
            return False
        if derive_deadline_type(ti) != "hard":
            return False
        return (task.priority or 2) >= cfg.min_displacer_priority

    async def _maybe_negotiate(self, task: TaskRow) -> None:
        """Run the negotiation loop after a deadline-clamped placement failure.

        Dispatches per the design §2 matrix: PROPOSED → send the Discord
        proposal (row 3); IMPOSSIBLE → row-6 options message (never silent);
        CalendarReadError → fallback alert (row 9). Soft / undated / disabled /
        below-floor tasks fall back to the existing ``needs_scheduling``
        surfacing (row caller already transitioned the state).
        """
        if self._calendar_client is None:
            # Fallback mode: no calendar to negotiate against. Short-circuit
            # before reading config so the calendar-less path stays simple.
            await self._notify_needs_scheduling(task)
            return
        cfg = self._scheduler._config.negotiation
        if not self._negotiation_gate(task, cfg):
            await self._notify_needs_scheduling(task)
            return

        try:
            outcome, proposal = await self._scheduler.negotiate_and_apply(
                task, self._db, self._calendar_client, self._calendar_id
            )
        except CalendarReadError as exc:
            if self._notification_service is not None:
                await self._notification_service.dispatch_fallback_alert(
                    component="negotiator",
                    error=f"Calendar read failed: {type(exc).__name__}: {exc}",
                    fallback="task left in needs_scheduling",
                    context={"task_id": task.id},
                )
            return

        if outcome == NEGOTIATION_PROPOSED and proposal is not None:
            await self._send_negotiation_proposal(task, proposal)
        elif outcome == NEGOTIATION_IMPOSSIBLE:
            await self._notify_negotiation_impossible(task)

    async def _notify_needs_scheduling(self, task: TaskRow) -> None:
        """Surface a needs_scheduling task via the existing digest/notify path."""
        if self._notification_service is None:
            return
        await self._notification_service.dispatch(
            notification_type=NOTIF_REMINDER,
            content=(
                f"Couldn't find a slot for '{task.title}' before its deadline — "
                "it's waiting for scheduling. I'll resurface it in the next plan."
            ),
            channel=CHANNEL_TASKS,
            priority=task.priority or 2,
        )

    async def _send_negotiation_proposal(self, task: TaskRow, proposal: Any) -> None:
        """Send the Discord proposal message + Accept/Decline/Pick-time view (row 3).

        Notification priority is ``max(task.priority, 3)`` per the §2 matrix.
        The view is wired with the scheduler/db/calendar so its Accept button
        can call back into the re-validated apply path.
        """
        if self._notification_service is None:
            return
        from donna.integrations.discord_views import NegotiationProposalView

        prio = max(task.priority or 2, 3)
        slot_fmt = proposal.slot.start.strftime("%A %-I:%M %p")
        move_lines = []
        for m in proposal.moves:
            old_fmt = m.old_start.strftime("%-I:%M %p")
            new_fmt = m.new_start.strftime("%A %-I:%M %p")
            move_lines.append(f"• move a task from {old_fmt} to {new_fmt}")
        moves_text = "\n".join(move_lines) if move_lines else "• (no moves)"
        content = (
            f"To fit '{task.title}' at {slot_fmt}, I'd need to rearrange:\n"
            f"{moves_text}\n"
            "Accept, decline, or pick another time?"
        )
        view = NegotiationProposalView(
            proposal_id=proposal.proposal_id,
            task_id=task.id,
            db=self._db,
            scheduler=self._scheduler,
            calendar_client=self._calendar_client,
            calendar_id=self._calendar_id,
            notification_service=self._notification_service,
        )
        await self._notification_service.dispatch(
            notification_type=NOTIF_RESCHEDULE,
            content=content,
            channel=CHANNEL_TASKS,
            priority=prio,
            view=view,
        )

    async def _notify_negotiation_impossible(self, task: TaskRow) -> None:
        """Surface the row-6 options message — never silent (design §2).

        Options at pri ≥4: take the next post-deadline slot, see which immovable
        item blocks, relax the deadline, or shorten the estimate.
        """
        if self._notification_service is None:
            return
        prio = max(task.priority or 2, 4)
        content = (
            f"I couldn't fit '{task.title}' before its deadline, even by "
            "rearranging — everything in the way is a fixed commitment.\n"
            "Options: take the next slot after the deadline, relax the deadline, "
            "or shorten the estimate."
        )
        await self._notification_service.dispatch(
            notification_type=NOTIF_RESCHEDULE,
            content=content,
            channel=CHANNEL_TASKS,
            priority=prio,
        )
