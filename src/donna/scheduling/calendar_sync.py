"""Calendar sync engine — polls Google Calendar and detects changes.

Runs as a long-lived asyncio loop. On each cycle:
  1. Fetches all events from configured calendars within the sync window.
  2. Compares against the local calendar_mirror table.
  3. Handles Donna-managed event changes:
     - Time changed  → update task.scheduled_start, increment reschedule_count
     - Event deleted → move task to backlog, queue notification
  4. Detects new user events that conflict with scheduled Donna tasks.
  5. Updates the mirror to reflect current state.

The polling interval comes from config/calendar.yaml — never hardcoded.
See docs/scheduling.md and slices/slice_04_calendar.md.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from donna.config import CalendarConfig
from donna.integrations.calendar import CalendarEvent, GoogleCalendarClient
from donna.tasks.database import Database
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()


class CalendarSync:
    """Polling-based calendar sync with change detection.

    Usage:
        sync = CalendarSync(client, db, config)
        await sync.run_once()           # single cycle (useful in tests)
        await sync.run_forever()        # production loop

    Args:
        client: Google Calendar client used to read live events.
        db: Task database (shared aiosqlite connection).
        config: Calendar configuration (poll interval, sync window, calendars).
        user_id: Owner of the calendar mirror rows.
        on_task_unscheduled: Optional callback invoked when a task is
            unscheduled because its event was deleted by the user.
        fallback_alert_fn: Optional coroutine forwarding to
            ``NotificationService.dispatch_fallback_alert`` (``component``,
            ``error``, ``fallback``, ``context`` kwargs). Called when a calendar
            read fails and the sync cycle is aborted to avoid mass-unscheduling.
    """

    def __init__(
        self,
        client: GoogleCalendarClient,
        db: Database,
        config: CalendarConfig,
        user_id: str = "nick",
        on_task_unscheduled: Any | None = None,
        fallback_alert_fn: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._db = db
        self._config = config
        self._user_id = user_id
        self._on_task_unscheduled = on_task_unscheduled
        self._fallback_alert_fn = fallback_alert_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Poll indefinitely. Interval is config.sync.poll_interval_seconds."""
        interval = self._config.sync.poll_interval_seconds
        logger.info("calendar_sync_started", poll_interval_seconds=interval)
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("calendar_sync_error")
            await asyncio.sleep(interval)

    async def run_once(self) -> None:
        """Execute a single sync cycle."""
        now = datetime.now(tz=UTC)
        time_min = now - timedelta(days=self._config.sync.lookbehind_days)
        time_max = now + timedelta(days=self._config.sync.lookahead_days)

        # Fetch live events from all configured calendars.
        live_events: dict[str, CalendarEvent] = {}
        failed_calendars: list[str] = []
        for cal_name, cal_cfg in self._config.calendars.items():
            cal_id = cal_cfg.calendar_id
            if not cal_id:
                continue
            try:
                events = await self._client.list_events(cal_id, time_min, time_max)
                for ev in events:
                    live_events[ev.event_id] = ev
            except Exception:
                logger.exception("calendar_fetch_error", calendar=cal_name)
                failed_calendars.append(cal_name)

        # Fail closed: if any calendar read failed, the live_events snapshot is
        # incomplete. Proceeding into change detection would misread every event
        # missing from the partial snapshot as "user deleted it" and mass-
        # unschedule tasks whose events still exist in Google. Abort this cycle
        # and retry next poll interval instead. Mirrors the fail-closed rationale
        # in scheduler.CalendarReadError (booking blind silently double-books).
        if failed_calendars:
            logger.warning(
                "calendar_sync_aborted_incomplete_read",
                event_type="fallback_activated",
                failed_calendars=failed_calendars,
            )
            if self._fallback_alert_fn is not None:
                await self._fallback_alert_fn(
                    component="calendar_sync",
                    error=(
                        "calendar read failed for: " + ", ".join(failed_calendars)
                    ),
                    fallback="skipped sync cycle (no change detection)",
                    context={"failed_calendars": failed_calendars},
                )
            return

        # Load local mirror.
        mirror = await self._load_mirror()

        # --- Detect changes on Donna-managed events ---
        for event_id, mirrored in mirror.items():
            if not mirrored["donna_managed"]:
                continue

            task_id = mirrored["donna_task_id"]
            if not task_id:
                continue

            if event_id not in live_events:
                # User deleted the event.
                await self._handle_event_deleted(event_id, task_id)
            else:
                live = live_events[event_id]
                mirror_start = _parse_dt_str(mirrored["start_time"])
                if abs((live.start - mirror_start).total_seconds()) > 60:
                    # User moved the event (>1 min difference).
                    await self._handle_time_changed(event_id, task_id, live.start, mirror_start)

        # --- Detect new user events that conflict with scheduled Donna tasks ---
        donna_scheduled = await self._load_scheduled_donna_tasks()
        for event_id, live in live_events.items():
            if live.donna_managed:
                continue
            if event_id in mirror:
                continue  # already known user event
            # Brand-new user event — check for conflicts.
            for task in donna_scheduled:
                t_start = _parse_dt_str(task["scheduled_start"])
                t_end = t_start + timedelta(minutes=task["estimated_duration"] or 60)
                if _overlaps(live.start, live.end, t_start, t_end):
                    await self._handle_conflict(live, task)

        # --- Update the mirror ---
        await self._update_mirror(live_events, now)

        logger.info("calendar_sync_complete", live_event_count=len(live_events))

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    async def _handle_event_deleted(self, event_id: str, task_id: str) -> None:
        """User deleted a Donna-managed event: move task to backlog."""
        task = await self._db.get_task(task_id)
        if task is None or task.status != TaskStatus.SCHEDULED.value:
            return

        await self._db.update_task(
            task_id,
            status=TaskStatus.BACKLOG,
            calendar_event_id=None,
            donna_managed=False,
            scheduled_start=None,
        )

        logger.info(
            "calendar_event_deleted_task_unscheduled",
            event_id=event_id,
            task_id=task_id,
        )

        if self._on_task_unscheduled:
            await self._on_task_unscheduled(task_id, "event_deleted")

    async def _handle_time_changed(
        self,
        event_id: str,
        task_id: str,
        new_start: datetime,
        old_start: datetime,
    ) -> None:
        """User moved a Donna event: implicit reschedule."""
        task = await self._db.get_task(task_id)
        if task is None:
            return

        new_count = (task.reschedule_count or 0) + 1
        await self._db.update_task(
            task_id,
            source="calendar_sync",
            scheduled_start=new_start,
            reschedule_count=new_count,
        )

        logger.info(
            "calendar_event_time_changed",
            event_id=event_id,
            task_id=task_id,
            old_start=old_start.isoformat(),
            new_start=new_start.isoformat(),
            reschedule_count=new_count,
        )

    async def _handle_conflict(
        self, user_event: CalendarEvent, donna_task: dict[str, Any]
    ) -> None:
        """New user event overlaps a Donna-scheduled task: auto-shift."""
        task_id = donna_task["id"]
        task = await self._db.get_task(task_id)
        if task is None:
            return

        logger.info(
            "calendar_conflict_detected",
            task_id=task_id,
            conflicting_event_id=user_event.event_id,
            user_event_summary=user_event.summary,
        )

        # Import here to avoid circular dependency at module level.
        from donna.scheduling.scheduler import NoSlotFoundError, Scheduler

        scheduler = Scheduler(self._config)
        personal_cal_id: Any = self._config.calendars.get("personal", {})
        if hasattr(personal_cal_id, "calendar_id"):
            cal_id = personal_cal_id.calendar_id
        else:
            cal_id = "primary"

        try:
            await scheduler.schedule_task(
                task=task,
                db=self._db,
                client=self._client,
                calendar_id=cal_id,
                force_reschedule=True,
            )
        except NoSlotFoundError:
            logger.warning(
                "calendar_conflict_no_slot_found",
                task_id=task_id,
            )

    # ------------------------------------------------------------------
    # Mirror management
    # ------------------------------------------------------------------

    async def _load_mirror(self) -> dict[str, dict[str, Any]]:
        """Load calendar_mirror rows keyed by event_id."""
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT event_id, calendar_id, summary, start_time, end_time, "
            "donna_managed, donna_task_id, etag, last_synced, attendees "
            "FROM calendar_mirror WHERE user_id = ?",
            (self._user_id,),
        )
        rows = await cursor.fetchall()
        return {
            row[0]: {
                "event_id": row[0],
                "calendar_id": row[1],
                "summary": row[2],
                "start_time": row[3],
                "end_time": row[4],
                "donna_managed": bool(row[5]),
                "donna_task_id": row[6],
                "etag": row[7],
                "last_synced": row[8],
                "attendees": row[9],
            }
            for row in rows
        }

    async def _update_mirror(
        self, live_events: dict[str, CalendarEvent], now: datetime
    ) -> None:
        """Upsert all live events into calendar_mirror; remove stale rows."""
        conn = self._db.connection
        now_str = now.isoformat()

        # Upsert live events.
        for ev in live_events.values():
            attendees_json = (
                json.dumps(list(ev.attendees)) if ev.attendees else None
            )
            await conn.execute(
                """
                INSERT INTO calendar_mirror
                    (event_id, user_id, calendar_id, summary, start_time,
                     end_time, donna_managed, donna_task_id, etag,
                     last_synced, attendees)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    calendar_id  = excluded.calendar_id,
                    summary      = excluded.summary,
                    start_time   = excluded.start_time,
                    end_time     = excluded.end_time,
                    donna_managed = excluded.donna_managed,
                    donna_task_id = excluded.donna_task_id,
                    etag         = excluded.etag,
                    last_synced  = excluded.last_synced,
                    attendees    = excluded.attendees
                """,
                (
                    ev.event_id,
                    self._user_id,
                    ev.calendar_id,
                    ev.summary,
                    ev.start.isoformat(),
                    ev.end.isoformat(),
                    int(ev.donna_managed),
                    ev.donna_task_id,
                    ev.etag,
                    now_str,
                    attendees_json,
                ),
            )

        if live_events:
            placeholders = ",".join("?" for _ in live_events)
            await conn.execute(
                "DELETE FROM calendar_mirror"
                f" WHERE user_id = ? AND event_id NOT IN ({placeholders})",
                [self._user_id, *live_events.keys()],
            )
        else:
            await conn.execute(
                "DELETE FROM calendar_mirror WHERE user_id = ?",
                (self._user_id,),
            )

        await conn.commit()

    async def _load_scheduled_donna_tasks(self) -> list[dict[str, Any]]:
        """Return tasks currently in 'scheduled' state with a calendar_event_id."""
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT id, scheduled_start, estimated_duration, priority "
            "FROM tasks WHERE status = ? AND donna_managed = 1 AND calendar_event_id IS NOT NULL",
            (TaskStatus.SCHEDULED.value,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "scheduled_start": row[1],
                "estimated_duration": row[2],
                "priority": row[3],
            }
            for row in rows
            if row[1] is not None  # scheduled_start must be set
        ]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_dt_str(value: str | None) -> datetime:
    """Parse an ISO datetime string (from SQLite) to a UTC-aware datetime."""
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Return True if interval [a_start, a_end) overlaps [b_start, b_end)."""
    return a_start < b_end and a_end > b_start
