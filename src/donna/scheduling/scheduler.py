"""Basic scheduling engine for Donna.

Given a task in backlog, finds the next available time slot that:
  - Respects domain time constraints (work hours, personal time, blackout, etc.)
  - Does not overlap existing calendar events (across ALL configured calendars)
  - Fits the estimated task duration
  - Honors the task's time intent (deadline / earliest / weekday constraint)

Creates a Google Calendar event with Donna extended properties and transitions
the task state from backlog → scheduled via the state machine.

All time constraints come from config/calendar.yaml — never hardcoded here.
Time-window hours are interpreted in the configured timezone (``calendar.yaml``
``timezone``): slot candidates are stepped in UTC (DST-safe) and converted to the
local zone for every window check, so the "absolute" blackout is enforced on the
user's wall clock, not UTC.

See docs/domain/scheduling.md and slices/slice_04_calendar.md.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog

from donna.config import CalendarConfig, TimeWindowConfig, TimeWindowsConfig
from donna.integrations.calendar import CalendarEvent, GoogleCalendarClient
from donna.scheduling.dependency_resolver import topological_sort
from donna.scheduling.time_intent import (
    TimeIntent,
    derive_deadline,
    derive_deadline_type,
)
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


class CalendarReadError(Exception):
    """Raised when a calendar read fails during placement.

    Placement aborts (fail-closed) rather than booking blind against an empty
    event list — which would silently double-book real meetings. Callers should
    surface this (fallback alert) and retry later, never proceed as if the
    calendar were free.
    """

    def __init__(self, calendar_id: str) -> None:
        self.calendar_id = calendar_id
        super().__init__(
            f"Failed to read calendar {calendar_id!r}; aborting placement "
            "to avoid booking against an unknown calendar state."
        )


@dataclasses.dataclass(frozen=True)
class ScheduledSlot:
    """A confirmed available time slot (timezone-aware, in the configured zone)."""

    start: datetime
    end: datetime


@dataclasses.dataclass(frozen=True)
class Move:
    """A single displacement: move a Donna-managed event to a free slot.

    Captures the *old* slot so the accept path can re-validate that the world
    has not drifted (design §1.6), and the *new* free slot the displaced task
    re-places into.

    Attributes:
        task_id: The displaced (victim) task's id.
        event_id: The victim's calendar event id (on the write calendar).
        old_start: Victim's current start (the slot being vacated).
        old_end: Victim's current end.
        new_start: The free slot the victim moves to.
        new_end: End of the new free slot.
        priority: Victim priority — drives the immediate-vs-digest notify rule.
    """

    task_id: str
    event_id: str
    old_start: datetime
    old_end: datetime
    new_start: datetime
    new_end: datetime
    priority: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the JSON-friendly shape stored in ``moves_json``."""
        return {
            "task_id": self.task_id,
            "event_id": self.event_id,
            "old_start": self.old_start.isoformat(),
            "old_end": self.old_end.isoformat(),
            "new_start": self.new_start.isoformat(),
            "new_end": self.new_end.isoformat(),
            "priority": self.priority,
        }


@dataclasses.dataclass(frozen=True)
class NegotiationProposal:
    """A displacement arrangement: T's slot + an ordered set of Moves (§1.1).

    Attributes:
        proposal_id: UUID; the persisted row's primary key.
        task_id: The displacer task T.
        slot: The slot T would take once the moves are applied.
        moves: Ordered displacements that free ``slot`` for T.
        cost: Total displacement cost (§1.4) — the bar the accept path may not
            exceed when re-negotiating after drift.
    """

    proposal_id: str
    task_id: str
    slot: ScheduledSlot
    moves: tuple[Move, ...]
    cost: float


# Outcome tokens returned by ``negotiate_and_apply`` (design §1.6 / §2 matrix).
NEGOTIATION_PROPOSED = "PROPOSED"
NEGOTIATION_APPLIED = "APPLIED"
NEGOTIATION_IMPOSSIBLE = "IMPOSSIBLE"


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
        # Window hours are local time; resolve the configured zone once.
        self._tz = ZoneInfo(config.timezone)
        # Serialize the read-modify-write placement section so two concurrent
        # placements (e.g. Discord + SMS) cannot pick the same slot. Realizes
        # spec_v3.md §3.7.1 ("calendar writes serialized to prevent double-booking").
        self._lock = asyncio.Lock()

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

        Candidates are stepped in UTC and converted to the configured local zone
        for every time-window check, so windows (work hours, blackout, …) are
        enforced on the user's wall clock. The search horizon is clamped to the
        task's deadline and starts no earlier than an explicit ``earliest`` bound.

        This is now "the first window-valid slot with zero overlaps": window /
        blackout / quiet / domain / weekday / earliest / deadline semantics live
        in :meth:`_iter_window_valid_slots`, and the only extra condition here is
        that the slot must not overlap any ``existing_events``. The negotiator
        reuses the same generator so window semantics never diverge (design §1.5).

        Args:
            task: The task to schedule.
            existing_events: All calendar events in the search window.
            now: Override for current time (useful in tests). Naive values are
                treated as UTC.

        Returns:
            ScheduledSlot with timezone-aware start/end in the configured zone.

        Raises:
            NoSlotFoundError: if no slot found before the deadline / horizon.
        """
        cfg = self._config.scheduling

        for slot in self._iter_window_valid_slots(task, now=now):
            if all(
                not _overlaps(slot.start, slot.end, ev.start, ev.end)
                for ev in existing_events
            ):
                return slot

        raise NoSlotFoundError(task.id, cfg.search_horizon_days)

    def _iter_window_valid_slots(
        self,
        task: TaskRow,
        now: datetime | None = None,
        horizon: datetime | None = None,
    ) -> Iterator[ScheduledSlot]:
        """Yield every window-valid candidate slot for *task*, IGNORING events.

        Each yielded slot satisfies the absolute/window constraints —
        blackout, quiet hours, domain window, weekday constraint, the
        ``earliest`` lower bound, and the deadline-clamped horizon — but is NOT
        checked against any calendar events. :meth:`find_next_slot` layers the
        zero-overlap test on top; the negotiator (design §1.5) instead inspects
        the blockers of each slot. Centralizing the window logic here keeps the
        two callers' window semantics identical.

        Args:
            task: The task whose window/deadline constraints define the search.
            now: Override for current time. Naive values are treated as UTC.
            horizon: Optional explicit upper bound. Capped by the configured
                search horizon and the task's derived deadline regardless; pass
                this only to search a *narrower* window.

        Yields:
            ScheduledSlot candidates in chronological order (timezone-aware,
            in the configured zone).
        """
        cfg = self._config.scheduling
        tw = self._config.time_windows

        now = _ensure_tz(now or datetime.now(tz=UTC)).replace(second=0, microsecond=0)
        duration = timedelta(minutes=task.estimated_duration or cfg.default_duration_minutes)
        step = timedelta(minutes=cfg.slot_step_minutes)
        search_end = now + timedelta(days=cfg.search_horizon_days)
        if horizon is not None:
            search_end = min(search_end, _ensure_tz(horizon))

        # Honor the task's time intent (interim placement guard — the full
        # constraint-aware negotiator is Plan 2). Clamp the horizon to the
        # deadline so an unplaceable dated task raises NoSlotFoundError (→
        # needs_scheduling) instead of being silently placed late or ASAP.
        ti = self._task_time_intent(task)
        deadline = derive_deadline(ti)
        if deadline is not None:
            search_end = min(search_end, _ensure_tz(deadline))

        weekday_constraint: set[int] | None = None
        if ti.kind == "constrained" and ti.constraints:
            wd = ti.constraints.get("weekday")
            if isinstance(wd, list) and wd:
                weekday_constraint = {int(d) for d in wd}

        candidate = _round_up(now, cfg.slot_step_minutes)
        if ti.kind in ("window", "constrained") and ti.earliest is not None:
            earliest = _round_up(_ensure_tz(ti.earliest), cfg.slot_step_minutes)
            if earliest > candidate:
                candidate = earliest

        domain = task.domain or TaskDomain.PERSONAL.value
        priority = task.priority or 2

        while candidate < search_end:
            slot_end = candidate + duration
            local_start = candidate.astimezone(self._tz)
            local_end = slot_end.astimezone(self._tz)

            if (
                weekday_constraint is None
                or local_start.weekday() in weekday_constraint
            ) and self._is_window_valid_slot(
                local_start, local_end, domain, priority, tw
            ):
                yield ScheduledSlot(start=local_start, end=local_end)

            candidate += step

    def _task_time_intent(self, task: TaskRow) -> TimeIntent:
        """Resolve the task's TimeIntent, falling back to a bare ``deadline``.

        Mirrors :meth:`AutoScheduler.on_task_created` so the slot-finder and the
        router agree on what "when" a task carries.
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

    def _is_window_valid_slot(
        self,
        start: datetime,
        end: datetime,
        domain: str,
        priority: int,
        tw: TimeWindowsConfig,
    ) -> bool:
        """Return True if [start, end) satisfies the time-window constraints.

        Checks blackout (absolute), quiet hours (soft — priority 5 only), and
        the domain window. Does NOT check calendar overlaps — that is layered on
        by :meth:`find_next_slot`, while the negotiator inspects blockers
        directly (design §1.5).

        ``start``/``end`` are local-zone datetimes; window checks read their
        local ``.hour``/``.weekday()``.
        """
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
        return self._slot_in_domain_window(start, end, domain, priority, tw)

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
    # Calendar busy-set (fail-closed across all configured calendars)
    # ------------------------------------------------------------------

    def _read_calendar_ids(self) -> list[str]:
        """All configured, non-empty calendar IDs (personal + work + family).

        Work/family are read-only and may be unset (empty) in config; those are
        skipped. Placement must consider every calendar the user is busy on, not
        only the personal write calendar.
        """
        return [c.calendar_id for c in self._config.calendars.values() if c.calendar_id]

    async def _gather_busy(
        self,
        client: GoogleCalendarClient,
        time_min: datetime,
        time_max: datetime,
    ) -> list[CalendarEvent]:
        """Union of events across all configured calendars (fail-closed).

        Raises:
            CalendarReadError: if any calendar read fails — placement aborts
                rather than booking against an unknown (possibly empty) state.
        """
        events: list[CalendarEvent] = []
        for cal_id in self._read_calendar_ids():
            try:
                events.extend(await client.list_events(cal_id, time_min, time_max))
            except Exception as exc:
                logger.exception("scheduler_list_events_failed", calendar_id=cal_id)
                raise CalendarReadError(cal_id) from exc
        return events

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

        The read→find→create section is serialized via an instance lock so
        concurrent placements cannot collide on the same slot.

        Args:
            task: Task to schedule (must be in backlog or scheduled for reschedule).
            db: Database instance.
            client: Authenticated GoogleCalendarClient.
            calendar_id: Target (write) Google Calendar ID — the personal calendar.
            force_reschedule: If True, treat as a reschedule (task may already be scheduled).

        Returns:
            The ScheduledSlot that was booked.

        Raises:
            NoSlotFoundError: if no slot fits before the deadline/horizon.
            CalendarReadError: if a calendar read fails (placement aborted).
        """
        cfg = self._config.scheduling

        async with self._lock:
            now = datetime.now(tz=UTC)
            time_max = now + timedelta(days=cfg.search_horizon_days)

            # Busy-set across ALL configured calendars; fail-closed on read error.
            existing_events = await self._gather_busy(client, now, time_max)

            slot = self.find_next_slot(task, existing_events, now=now)

            # Create Google Calendar event on the personal (write) calendar.
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
            calendar_id: Target (write) Google Calendar ID.

        Returns:
            List of ScheduledSlots in the order tasks were scheduled.

        Raises:
            CalendarReadError: if a calendar read fails (placement aborted).
        """
        ordered = topological_sort(tasks)

        cfg = self._config.scheduling

        async with self._lock:
            now = datetime.now(tz=UTC)
            time_max = now + timedelta(days=cfg.search_horizon_days)

            # Fail-closed busy-set across all configured calendars.
            existing_events = await self._gather_busy(client, now, time_max)

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
    # Negotiation (single-displacement, propose-and-confirm — design §1, Slice A)
    # ------------------------------------------------------------------

    def _task_priority(self, task: TaskRow) -> int:
        """Priority with the same fallback the slot-finder uses (default 2)."""
        return task.priority or 2

    def _task_is_hard(self, task: TaskRow) -> bool:
        """True if *task* carries a hard deadline (exact/window/constrained)."""
        return derive_deadline_type(self._task_time_intent(task)) == "hard"

    async def _movable(
        self,
        ev: CalendarEvent,
        displacer: TaskRow,
        db: Database,
        write_calendar_id: str,
        now: datetime,
    ) -> TaskRow | None:
        """Return the backing task if *ev* may be displaced for *displacer*, else None.

        Implements the movability hard filter (design §1.3). A blocker is
        movable iff ALL hold:

        - ``ev.donna_managed`` is True and ``ev.donna_task_id`` resolves to a
          task row;
        - ``ev.calendar_id`` is the personal **write** calendar (work/family
          read-only calendars are immovable, no override);
        - the backing task status is ``scheduled`` (never in_progress/done/…);
        - ``ev.start - now >= min_lead_minutes``;
        - the victim is strictly lower priority than the displacer, OR
          equal-priority while the victim is soft/undated and the displacer is
          hard (OD-1 conservative default);
        - the victim has not been auto-moved ``max_auto_moves_per_task_per_day``
          times today.

        **Any user-created (non-``donna_managed``) event is immovable. Anything
        on a read-only calendar is immovable. No override knob exists.**

        The simulation-feasibility check (can the victim actually re-place) is
        done by the caller against the live busy set, not here.
        """
        cfg = self._config.negotiation

        # NEVER movable: user events or anything off the personal write calendar.
        if not ev.donna_managed:
            return None
        if ev.calendar_id != write_calendar_id:
            return None
        if not ev.donna_task_id:
            return None

        victim = await db.get_task(ev.donna_task_id)
        if victim is None:
            return None
        if victim.status != TaskStatus.SCHEDULED.value:
            return None

        # Minimum lead time before the event starts.
        lead = _ensure_tz(ev.start) - now
        if lead < timedelta(minutes=cfg.min_lead_minutes):
            return None

        # Priority eligibility (OD-1): strictly lower, or equal-priority-soft
        # while the displacer is hard.
        d_prio = self._task_priority(victim)
        t_prio = self._task_priority(displacer)
        strictly_lower = d_prio < t_prio
        equal_soft_vs_hard = (
            d_prio == t_prio
            and not self._task_is_hard(victim)
            and self._task_is_hard(displacer)
        )
        if not (strictly_lower or equal_soft_vs_hard):
            return None

        # Anti-thrash: cap auto-moves per task per day.
        moved_today = await self._auto_moves_today(victim.id, db, now)
        if moved_today >= cfg.max_auto_moves_per_task_per_day:
            return None

        return victim

    async def _auto_moves_today(
        self, task_id: str, db: Database, now: datetime
    ) -> int:
        """Count proposals applied today that moved *task_id* (anti-thrash).

        Counts ``accepted`` proposals created since local midnight whose
        ``moves_json`` includes *task_id*. A conservative over-count is fine —
        the cap only gets stricter, never looser, which preserves the no-thrash
        guarantee (design §1.3).
        """
        local_midnight = now.astimezone(self._tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        try:
            rows = await db.execute_sql(
                """SELECT moves_json FROM negotiation_proposals
                   WHERE status = 'accepted' AND created_at >= ?""",
                [local_midnight.isoformat()],
            )
        except Exception:
            # No proposals table yet / read error: treat as zero moves but never
            # silently — the count only affects the anti-thrash cap.
            logger.warning(
                "auto_moves_count_failed",
                event_type="fallback_activated",
                task_id=task_id,
            )
            return 0
        count = 0
        for row in rows:
            try:
                moves = json.loads(row["moves_json"])
            except (TypeError, ValueError, KeyError):
                continue
            if any(m.get("task_id") == task_id for m in moves):
                count += 1
        return count

    def _slack_hours(
        self, victim: TaskRow, new_end: datetime, now: datetime
    ) -> float:
        """Hours between the victim's re-placed end and its own deadline.

        ``inf`` for soft/undated victims (no deadline pressure). Used by the
        cost function (§1.4) — a tighter slack makes a worse victim.
        """
        deadline = derive_deadline(self._task_time_intent(victim))
        if deadline is None:
            return float("inf")
        return (_ensure_tz(deadline) - new_end).total_seconds() / 3600.0

    def _displacement_cost(
        self, victim: TaskRow, new_end: datetime, now: datetime
    ) -> float:
        """Scalar displacement cost for one victim (design §1.4; lower = better).

        ``cost(D) = W_PRIO*priority + W_HARD*(hard?) + W_SLACK/(1+slack)
                    + W_RESCH*reschedule_count + W_SOON/(1+hrs_until_start)``

        Weights come from ``negotiation.cost_weights`` (config, not code).
        """
        w = self._config.negotiation.cost_weights
        prio = self._task_priority(victim)
        hard = 1.0 if self._task_is_hard(victim) else 0.0
        slack = self._slack_hours(victim, new_end, now)
        slack_term = 0.0 if slack == float("inf") else w.slack / (1.0 + max(slack, 0.0))
        reschedule_count = victim.reschedule_count or 0

        start = victim.scheduled_start
        if start:
            hrs_until = max(
                0.0,
                (_ensure_tz(datetime.fromisoformat(start)) - now).total_seconds()
                / 3600.0,
            )
        else:
            hrs_until = 0.0
        imminence_term = w.imminence / (1.0 + hrs_until)

        return (
            w.priority * prio
            + w.hard_deadline * hard
            + slack_term
            + w.reschedule * reschedule_count
            + imminence_term
        )

    async def negotiate_placement(
        self,
        task: TaskRow,
        db: Database,
        client: GoogleCalendarClient,
        write_calendar_id: str,
        now: datetime | None = None,
    ) -> NegotiationProposal | None:
        """Search the pre-deadline window for a feasible displacement (design §1.5).

        **PRECONDITION: the caller already holds ``self._lock``.** This method
        never re-acquires it (the lock is not reentrant) — :meth:`negotiate_and_apply`
        is the locked entry point.

        Builds the busy set via :meth:`_gather_busy` (fail-closed — a
        :class:`CalendarReadError` propagates). Scans window-valid slots; for
        each, computes movable blockers and skips any slot with an immovable
        blocker or more than ``max_displacements_per_placement`` blockers. Ranks
        candidates by the cost function (§1.4) and, for the cheapest, simulates
        re-placing each displaced task into a genuinely FREE slot via
        :meth:`find_next_slot` (structural depth-1 — a re-placed task lands in
        free space and so can never displace anything). Returns the first
        feasible proposal, or ``None`` if none exists.

        Args:
            task: The displacer task T that failed clean placement.
            db: Database (used to resolve victim task rows).
            client: Authenticated calendar client.
            write_calendar_id: The personal write calendar id.
            now: Override for current time (tests). Naive values treated as UTC.

        Returns:
            A :class:`NegotiationProposal`, or ``None`` if no feasible
            single-displacement arrangement exists.

        Raises:
            CalendarReadError: if a calendar read fails (fail-closed).
        """
        cfg = self._config.negotiation
        sched_cfg = self._config.scheduling
        now = _ensure_tz(now or datetime.now(tz=UTC)).replace(second=0, microsecond=0)
        horizon = now + timedelta(days=sched_cfg.search_horizon_days)
        deadline = derive_deadline(self._task_time_intent(task))
        search_end = min(horizon, _ensure_tz(deadline)) if deadline else horizon

        # Fail-closed busy-set across all calendars (precondition: under lock).
        busy = await self._gather_busy(client, now, search_end)

        # Collect candidate slots whose only blockers are movable, capped count.
        # Each entry: (slot_cost, slot.start, slot, [(event, victim_task)]).
        candidates: list[
            tuple[float, datetime, ScheduledSlot, list[tuple[CalendarEvent, TaskRow]]]
        ] = []
        deadline_tz = _ensure_tz(deadline) if deadline else None
        for slot in self._iter_window_valid_slots(task, now=now, horizon=deadline):
            # Negotiation is hard-deadline-only — never accept a slot that ENDS
            # past the deadline (``_iter_window_valid_slots`` clamps only the
            # slot *start*, matching find_next_slot; for the negotiator we hold
            # the stronger guarantee so a hard deadline is never silently blown).
            if deadline_tz is not None and slot.end > deadline_tz:
                continue
            blockers = [
                ev
                for ev in busy
                if _overlaps(slot.start, slot.end, ev.start, ev.end)
            ]
            if not blockers:
                # A genuinely free slot — find_next_slot would have taken it.
                # (Reached only if the caller called us speculatively; return a
                # zero-cost, zero-move proposal so the slot is still usable.)
                return NegotiationProposal(
                    proposal_id=str(uuid.uuid4()),
                    task_id=task.id,
                    slot=slot,
                    moves=(),
                    cost=0.0,
                )
            if len(blockers) > cfg.max_displacements_per_placement:
                continue

            resolved: list[tuple[CalendarEvent, TaskRow]] = []
            all_movable = True
            for ev in blockers:
                victim = await self._movable(
                    ev, task, db, write_calendar_id, now
                )
                if victim is None:
                    all_movable = False
                    break
                resolved.append((ev, victim))
            if not all_movable:
                continue

            slot_cost = sum(
                self._displacement_cost(v, slot.end, now) for _ev, v in resolved
            )
            candidates.append((slot_cost, slot.start, slot, resolved))

        # Cheapest first; ties broken by earliest slot (Get It Done, §1.4).
        candidates.sort(key=lambda c: (c[0], c[1]))

        for slot_cost, _start, slot, resolved in candidates:
            # Simulate: remove the displaced events, add T@slot, then re-place
            # each victim into a FREE slot (depth-1, structural termination).
            blocker_ids = {ev.event_id for ev, _v in resolved}
            sim: list[CalendarEvent] = [
                ev for ev in busy if ev.event_id not in blocker_ids
            ]
            sim.append(_synthetic_event(task.id, slot, write_calendar_id))

            moves: list[Move] = []
            feasible = True
            # Re-place victims cheapest-first so the tightest victims get the
            # earliest free slots.
            ordered = sorted(
                resolved, key=lambda r: self._displacement_cost(r[1], slot.end, now)
            )
            for ev, victim in ordered:
                try:
                    new_slot = self.find_next_slot(victim, sim, now=now)
                except NoSlotFoundError:
                    feasible = False
                    break
                moves.append(
                    Move(
                        task_id=victim.id,
                        event_id=ev.event_id,
                        old_start=_ensure_tz(ev.start),
                        old_end=_ensure_tz(ev.end),
                        new_start=new_slot.start,
                        new_end=new_slot.end,
                        priority=self._task_priority(victim),
                    )
                )
                sim.append(
                    _synthetic_event(victim.id, new_slot, write_calendar_id)
                )

            if feasible:
                return NegotiationProposal(
                    proposal_id=str(uuid.uuid4()),
                    task_id=task.id,
                    slot=slot,
                    moves=tuple(moves),
                    cost=slot_cost,
                )

        return None

    async def negotiate_and_apply(
        self,
        task: TaskRow,
        db: Database,
        client: GoogleCalendarClient,
        write_calendar_id: str,
        now: datetime | None = None,
    ) -> tuple[str, NegotiationProposal | None]:
        """Negotiate a slot and, in Slice A, always persist + propose (design §1.6).

        Serialized under ``self._lock`` (the same lock as :meth:`schedule_task`;
        NOT reentrant — internal helpers must not re-acquire). Because
        ``auto_apply`` is ``false`` in Slice A, a successful negotiation is
        ALWAYS persisted as a pending proposal and surfaced for confirmation —
        moves are never applied silently (2026-06-05 confirmation invariant).

        Args:
            task: The displacer task T.
            db: Database.
            client: Authenticated calendar client.
            write_calendar_id: Personal write calendar id.
            now: Override for current time (tests).

        Returns:
            ``(NEGOTIATION_PROPOSED, proposal)`` when a feasible proposal was
            found and persisted, or ``(NEGOTIATION_IMPOSSIBLE, None)`` when no
            arrangement exists.

        Raises:
            CalendarReadError: if a calendar read fails (fail-closed).
        """
        async with self._lock:
            now = _ensure_tz(now or datetime.now(tz=UTC)).replace(
                second=0, microsecond=0
            )
            proposal = await self.negotiate_placement(
                task, db, client, write_calendar_id, now=now
            )
            if proposal is None:
                logger.info("negotiation_impossible", task_id=task.id)
                return NEGOTIATION_IMPOSSIBLE, None

            # Slice A: auto_apply is OFF → persist and propose, never auto-apply.
            # (Slice B will gate self._apply behind auto_apply + needs_confirm.)
            ttl = timedelta(hours=self._config.negotiation.proposal_ttl_hours)
            await db.create_negotiation_proposal(
                proposal_id=proposal.proposal_id,
                task_id=proposal.task_id,
                slot_start=proposal.slot.start.isoformat(),
                slot_end=proposal.slot.end.isoformat(),
                moves_json=json.dumps([m.to_dict() for m in proposal.moves]),
                cost=proposal.cost,
                created_at=now.isoformat(),
                expires_at=(now + ttl).isoformat(),
            )
            logger.info(
                "negotiation_proposed",
                task_id=task.id,
                proposal_id=proposal.proposal_id,
                num_moves=len(proposal.moves),
                cost=proposal.cost,
            )
            return NEGOTIATION_PROPOSED, proposal

    async def _apply(
        self,
        proposal: NegotiationProposal,
        task: TaskRow,
        db: Database,
        client: GoogleCalendarClient,
        write_calendar_id: str,
        now: datetime | None = None,
    ) -> str:
        """Apply an accepted proposal under the lock with re-validation (design §1.6).

        **PRECONDITION: the caller already holds ``self._lock``** (the accept
        button handler acquires it). Re-reads the busy set and verifies that
        each ``move.old`` still matches and that ``move.new`` + T's slot are
        still free. On drift, re-negotiates once and applies only if the new
        cost is ``<=`` the approved cost; otherwise re-proposes (persists the
        fresh proposal) and returns ``NEGOTIATION_PROPOSED`` without writing.

        Moves are applied BEFORE T's create (crash-safety: a crash leaves
        displaced tasks in valid free slots and T still ``needs_scheduling``).
        Displaced tasks stay ``scheduled`` with ``reschedule_count`` +1; T uses
        the existing ``needs_scheduling → scheduled`` transition.

        Args:
            proposal: The accepted proposal to apply.
            task: The displacer task T.
            db: Database.
            client: Authenticated calendar client.
            write_calendar_id: Personal write calendar id.
            now: Override for current time (tests).

        Returns:
            ``NEGOTIATION_APPLIED`` on success, ``NEGOTIATION_PROPOSED`` if a
            re-negotiation produced a fresh proposal, or
            ``NEGOTIATION_IMPOSSIBLE`` if the world drifted and nothing feasible
            remains.

        Raises:
            CalendarReadError: if the re-read fails (fail-closed).
        """
        now = _ensure_tz(now or datetime.now(tz=UTC)).replace(second=0, microsecond=0)
        sched_cfg = self._config.scheduling
        horizon = now + timedelta(days=sched_cfg.search_horizon_days)
        deadline = derive_deadline(self._task_time_intent(task))
        search_end = min(horizon, _ensure_tz(deadline)) if deadline else horizon

        # Re-read busy to detect drift since the proposal was made.
        busy = await self._gather_busy(client, now, search_end)
        by_event = {ev.event_id: ev for ev in busy}

        drift = False
        # Each victim's old slot must still match its current calendar event.
        for m in proposal.moves:
            ev = by_event.get(m.event_id)
            if (
                ev is None
                or _ensure_tz(ev.start) != m.old_start
                or _ensure_tz(ev.end) != m.old_end
            ):
                drift = True
                break

        if not drift:
            # T's target slot and every new victim slot must still be free,
            # ignoring exactly the events we are about to vacate.
            vacating = {m.event_id for m in proposal.moves}
            free_set = [ev for ev in busy if ev.event_id not in vacating]
            if any(
                _overlaps(proposal.slot.start, proposal.slot.end, ev.start, ev.end)
                for ev in free_set
            ):
                drift = True
            else:
                for m in proposal.moves:
                    occupied = [
                        ev
                        for ev in free_set
                        if ev.event_id not in vacating
                        and _overlaps(m.new_start, m.new_end, ev.start, ev.end)
                    ]
                    if occupied:
                        drift = True
                        break

        if drift:
            logger.info("negotiation_apply_drift_renegotiating", task_id=task.id)
            fresh = await self.negotiate_placement(
                task, db, client, write_calendar_id, now=now
            )
            if fresh is None:
                return NEGOTIATION_IMPOSSIBLE
            if fresh.cost <= proposal.cost:
                # Within the approved cost — apply the fresh arrangement.
                proposal = fresh
            else:
                # Costlier than approved — re-propose, do not apply.
                ttl = timedelta(hours=self._config.negotiation.proposal_ttl_hours)
                await db.create_negotiation_proposal(
                    proposal_id=fresh.proposal_id,
                    task_id=fresh.task_id,
                    slot_start=fresh.slot.start.isoformat(),
                    slot_end=fresh.slot.end.isoformat(),
                    moves_json=json.dumps([m.to_dict() for m in fresh.moves]),
                    cost=fresh.cost,
                    created_at=now.isoformat(),
                    expires_at=(now + ttl).isoformat(),
                )
                logger.info(
                    "negotiation_apply_reproposed",
                    task_id=task.id,
                    proposal_id=fresh.proposal_id,
                )
                return NEGOTIATION_PROPOSED

        # --- Apply: moves FIRST (each lands in free space), then T's create. ---
        for m in proposal.moves:
            await client.update_event(
                write_calendar_id, m.event_id, m.new_start, m.new_end
            )
            victim = await db.get_task(m.task_id)
            await db.update_task(
                m.task_id,
                scheduled_start=m.new_start,
                reschedule_count=(victim.reschedule_count or 0) + 1
                if victim is not None
                else 1,
            )
            logger.info(
                "negotiation_victim_moved",
                task_id=m.task_id,
                event_id=m.event_id,
                new_start=m.new_start.isoformat(),
            )

        event = await client.create_event(
            calendar_id=write_calendar_id,
            summary=task.title,
            start=proposal.slot.start,
            end=proposal.slot.end,
            task_id=task.id,
        )
        # T uses the existing needs_scheduling → scheduled transition
        # (alternative_or_rearrange_accepted). No state added.
        if task.status == TaskStatus.NEEDS_SCHEDULING.value:
            await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        await db.update_task(
            task.id,
            scheduled_start=proposal.slot.start,
            calendar_event_id=event.event_id,
            donna_managed=True,
        )
        await db.update_negotiation_proposal_status(
            proposal.proposal_id, "accepted"
        )
        logger.info(
            "negotiation_applied",
            task_id=task.id,
            proposal_id=proposal.proposal_id,
            num_moves=len(proposal.moves),
        )
        return NEGOTIATION_APPLIED


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _synthetic_event(
    task_id: str, slot: ScheduledSlot, calendar_id: str
) -> CalendarEvent:
    """Build an in-memory CalendarEvent for a slot during simulation.

    Used by the negotiator to add T (and re-placed victims) into the simulated
    busy set so subsequent re-placements see them as occupied. Never persisted.
    """
    return CalendarEvent(
        event_id=f"sim-{task_id}",
        calendar_id=calendar_id,
        summary="(simulated)",
        start=slot.start,
        end=slot.end,
        donna_managed=True,
        donna_task_id=task_id,
        etag="",
    )


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
