"""Basic scheduling engine for Donna.

Given a task in backlog, finds the next available time slot that:
  - Respects domain time constraints (work hours, personal time, blackout, etc.)
  - Does not overlap existing calendar events
  - Fits the estimated task duration

Creates a Google Calendar event with Donna extended properties and transitions
the task state from backlog → scheduled via the state machine.

All time constraints come from config/calendar.yaml — never hardcoded here.
See docs/scheduling.md and slices/slice_04_calendar.md.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from donna.config import CalendarConfig, TimeWindowConfig, TimeWindowsConfig
from donna.integrations.calendar import CalendarEvent, GoogleCalendarClient
from donna.scheduling.dependency_resolver import topological_sort
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskDomain, TaskStatus

logger = structlog.get_logger()


class NoSlotFoundError(Exception):
    """Raised when the scheduler cannot find a valid slot within the search horizon."""

    def __init__(self, task_id: str, horizon_days: int) -> None:
        self.task_id = task_id
        super().__init__(
            f"No available slot found for task {task_id} "
            f"within {horizon_days}-day horizon."
        )


@dataclasses.dataclass(frozen=True)
class ScheduledSlot:
    """A confirmed available time slot."""

    start: datetime
    end: datetime


class Scheduler:
    """Finds the next available slot for a task and schedules it.

    Usage:
        scheduler = Scheduler(config)

        # Pure computation — find a slot without side effects.
        slot = scheduler.find_next_slot(task, existing_events)

        # Full scheduling flow — creates calendar event, transitions state, updates DB.
        slot = await scheduler.schedule_task(task, db, client, calendar_id)
    """

    def __init__(self, config: CalendarConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Slot finding (pure, synchronous)
    # ------------------------------------------------------------------

    def find_next_slot(
        self,
        task: TaskRow,
        existing_events: list[CalendarEvent],
        now: datetime | None = None,
    ) -> ScheduledSlot:
        """Find the first available slot for *task* given *existing_events*.

        Args:
            task: The task to schedule.
            existing_events: All calendar events in the search window.
            now: Override for current time (useful in tests).

        Returns:
            ScheduledSlot with start/end datetimes.

        Raises:
            NoSlotFoundError: if no slot found within search_horizon_days.
        """
        cfg = self._config.scheduling
        tw = self._config.time_windows

        now = (now or datetime.now(tz=UTC)).replace(second=0, microsecond=0)
        duration = timedelta(minutes=task.estimated_duration or cfg.default_duration_minutes)
        step = timedelta(minutes=cfg.slot_step_minutes)
        horizon = now + timedelta(days=cfg.search_horizon_days)

        # Round up to the next slot boundary.
        candidate = _round_up(now, cfg.slot_step_minutes)

        # Determine which time windows are allowed for this task's domain.
        domain = task.domain or TaskDomain.PERSONAL.value
        priority = task.priority or 2

        while candidate < horizon:
            slot_end = candidate + duration

            if self._is_valid_slot(candidate, slot_end, domain, priority, tw, existing_events):
                return ScheduledSlot(start=candidate, end=slot_end)

            candidate += step

        raise NoSlotFoundError(task.id, cfg.search_horizon_days)

    def _is_valid_slot(
        self,
        start: datetime,
        end: datetime,
        domain: str,
        priority: int,
        tw: TimeWindowsConfig,
        existing_events: list[CalendarEvent],
    ) -> bool:
        """Return True if [start, end) is a valid scheduling window."""
        # Check boundary points for hard constraints.
        for check_dt in _boundary_points(start, end):
            hour = check_dt.hour
            weekday = check_dt.weekday()  # 0=Mon … 6=Sun

            # --- Blackout (absolute — no exceptions) ---
            if _in_window(hour, weekday, tw.blackout):
                return False

            # --- Quiet hours (soft — only priority 5 allowed) ---
            if _in_window(hour, weekday, tw.quiet_hours) and priority < 5:
                return False

        # --- Domain window check ---
        # The entire slot must fall inside the domain-allowed window.
        # Priority-5 tasks may also use quiet-hours window (8pm–midnight).
        if not self._slot_in_domain_window(start, end, domain, priority, tw):
            return False

        # --- Calendar overlap check ---
        return all(not _overlaps(start, end, ev.start, ev.end) for ev in existing_events)

    def _slot_in_domain_window(
        self,
        start: datetime,
        end: datetime,
        domain: str,
        priority: int,
        tw: TimeWindowsConfig,
    ) -> bool:
        """Return True if the slot is within the permitted window for domain."""
        start_hour = start.hour
        end_hour_excl = end.hour if end.minute == 0 else end.hour + 1
        weekday = start.weekday()

        # Priority-5 tasks may use quiet hours (8pm–midnight) as a scheduling window.
        if priority == 5:
            in_quiet = (
                _in_window(start_hour, weekday, tw.quiet_hours)
                and end_hour_excl <= tw.quiet_hours.end_hour
            )
            if in_quiet:
                return True

        if domain == TaskDomain.WORK.value:
            # Work tasks: work window only.
            return (
                _in_window(start_hour, weekday, tw.work)
                and end.weekday() == weekday  # don't span days
                and end_hour_excl <= tw.work.end_hour
            )
        elif domain == TaskDomain.FAMILY.value:
            # Family tasks: personal window (baby time blocks handled via calendar events).
            return (
                _in_window(start_hour, weekday, tw.personal)
                and end_hour_excl <= tw.personal.end_hour
            ) or (
                weekday in tw.weekend.days
                and _in_window(start_hour, weekday, tw.weekend)
                and end_hour_excl <= tw.weekend.end_hour
            )
        else:
            # Personal tasks: personal window or weekends.
            return (
                _in_window(start_hour, weekday, tw.personal)
                and end_hour_excl <= tw.personal.end_hour
            ) or (
                weekday in tw.weekend.days
                and _in_window(start_hour, weekday, tw.weekend)
                and end_hour_excl <= tw.weekend.end_hour
            )

    # ------------------------------------------------------------------
    # Full scheduling flow (async, with side effects)
    # ------------------------------------------------------------------

    async def schedule_task(
        self,
        task: TaskRow,
        db: Database,
        client: GoogleCalendarClient,
        calendar_id: str,
        force_reschedule: bool = False,
    ) -> ScheduledSlot:
        """Schedule a task: find slot, create event, transition state, update DB.

        Args:
            task: Task to schedule (must be in backlog or scheduled for reschedule).
            db: Database instance.
            client: Authenticated GoogleCalendarClient.
            calendar_id: Target Google Calendar ID.
            force_reschedule: If True, treat as a reschedule (task may already be scheduled).

        Returns:
            The ScheduledSlot that was booked.
        """
        cfg = self._config.scheduling
        now = datetime.now(tz=UTC)
        time_max = now + timedelta(days=cfg.search_horizon_days)

        # Fetch existing events to check for overlaps.
        try:
            existing_events = await client.list_events(calendar_id, now, time_max)
        except Exception:
            logger.exception("scheduler_list_events_failed", task_id=task.id)
            existing_events = []

        slot = self.find_next_slot(task, existing_events, now=now)

        # Create Google Calendar event.
        event = await client.create_event(
            calendar_id=calendar_id,
            summary=task.title,
            start=slot.start,
            end=slot.end,
            task_id=task.id,
        )

        # Delete old calendar event if rescheduling.
        if force_reschedule and task.calendar_event_id:
            try:
                await client.delete_event(calendar_id, task.calendar_event_id)
            except Exception:
                logger.warning(
                    "scheduler_delete_old_event_failed",
                    task_id=task.id,
                    old_event_id=task.calendar_event_id,
                )

        # Transition task state via state machine.
        if task.status == TaskStatus.BACKLOG.value:
            await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        elif force_reschedule and task.status == TaskStatus.SCHEDULED.value:
            # Already scheduled — just update fields, no state transition needed.
            pass

        # Update task with calendar details.
        update_fields: dict[str, Any] = dict(
            scheduled_start=slot.start,
            calendar_event_id=event.event_id,
            donna_managed=True,
        )
        if force_reschedule:
            update_fields["reschedule_count"] = (task.reschedule_count or 0) + 1

        await db.update_task(task.id, **update_fields)

        logger.info(
            "task_scheduled",
            task_id=task.id,
            slot_start=slot.start.isoformat(),
            slot_end=slot.end.isoformat(),
            event_id=event.event_id,
            calendar_id=calendar_id,
            force_reschedule=force_reschedule,
        )

        return slot


    async def schedule_dependency_chain(
        self,
        tasks: list[TaskRow],
        db: Database,
        client: GoogleCalendarClient,
        calendar_id: str,
    ) -> list[ScheduledSlot]:
        """Schedule a list of tasks respecting their dependency order.

        Tasks are sorted topologically (blockers first). Each dependent task
        is scheduled to start no earlier than the end of its most recently
        scheduled direct blocker, ensuring sequential execution.

        Args:
            tasks: Tasks to schedule (may be in any order).
            db: Database instance.
            client: Authenticated GoogleCalendarClient.
            calendar_id: Target Google Calendar ID.

        Returns:
            List of ScheduledSlots in the order tasks were scheduled.
        """
        ordered = topological_sort(tasks)

        # Fetch all existing calendar events once.
        cfg = self._config.scheduling
        now = datetime.now(tz=UTC)
        time_max = now + timedelta(days=cfg.search_horizon_days)

        try:
            existing_events = await client.list_events(calendar_id, now, time_max)
        except Exception:
            logger.exception("scheduler_chain_list_events_failed")
            existing_events = []

        # Track slot end times per task ID for dependency enforcement.
        slot_ends: dict[str, datetime] = {}
        booked_slots: list[ScheduledSlot] = []

        from donna.scheduling.dependency_resolver import _parse_deps

        for task in ordered:
            # Determine the earliest start based on direct blockers.
            dep_ids = _parse_deps(task.dependencies)
            after_dt = now
            for dep_id in dep_ids:
                if dep_id in slot_ends:
                    after_dt = max(after_dt, slot_ends[dep_id])

            slot = self.find_next_slot(task, existing_events, now=after_dt)

            # Create calendar event.
            event = await client.create_event(
                calendar_id=calendar_id,
                summary=task.title,
                start=slot.start,
                end=slot.end,
                task_id=task.id,
            )

            # Transition state and update DB.
            if task.status == TaskStatus.BACKLOG.value:
                await db.transition_task_state(task.id, TaskStatus.SCHEDULED)

            await db.update_task(
                task.id,
                scheduled_start=slot.start,
                calendar_event_id=event.event_id,
                donna_managed=True,
            )

            # Register this slot's end so dependents can use it.
            slot_ends[task.id] = slot.end

            # Add the new event to the list so subsequent tasks see it.
            existing_events.append(event)

            booked_slots.append(slot)
            logger.info(
                "chain_task_scheduled",
                task_id=task.id,
                slot_start=slot.start.isoformat(),
                slot_end=slot.end.isoformat(),
            )

        return booked_slots


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _round_up(dt: datetime, step_minutes: int) -> datetime:
    """Round dt up to the next multiple of step_minutes."""
    remainder = dt.minute % step_minutes
    if remainder == 0 and dt.second == 0:
        return dt
    delta = timedelta(minutes=(step_minutes - remainder))
    return (dt + delta).replace(second=0, microsecond=0)


def _in_window(hour: int, weekday: int, window: TimeWindowConfig) -> bool:
    """Return True if hour/weekday falls within the time window."""
    day_ok = not window.days or weekday in window.days
    hour_ok = window.start_hour <= hour < window.end_hour
    return day_ok and hour_ok


def _boundary_points(start: datetime, end: datetime) -> list[datetime]:
    """Return the start and the minute just before end (for constraint checking)."""
    points = [start]
    if end > start + timedelta(minutes=1):
        points.append(end - timedelta(minutes=1))
    return points


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Return True if interval [a_start, a_end) overlaps [b_start, b_end)."""
    # Ensure both are tz-aware or both naive before comparing.
    a_start, a_end = _ensure_tz(a_start), _ensure_tz(a_end)
    b_start, b_end = _ensure_tz(b_start), _ensure_tz(b_end)
    return a_start < b_end and a_end > b_start


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
