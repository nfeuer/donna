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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite
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
    """

    def __init__(
        self,
        client: GoogleCalendarClient,
        db: Database,
        config: CalendarConfig,
        # Injected for testing — called when a task is moved to backlog after deletion.
        on_task_unscheduled: Any | None = None,
    ) -> None:
        self._client = client
        self._db = db
        self._config = config
        self._on_task_unscheduled = on_task_unscheduled  # async callable(task_id, reason)

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
        now = datetime.now(tz=timezone.utc)
        time_min = now - timedelta(days=self._config.sync.lookbehind_days)
        time_max = now + timedelta(days=self._config.sync.lookahead_days)

        # Fetch live events from all configured calendars.
        live_events: dict[str, CalendarEvent] = {}
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

        await self._log_correction(
            task_id=task_id,
            field="scheduled_start",
            original=old_start.isoformat(),
            corrected=new_start.isoformat(),
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
        personal_cal_id = self._config.calendars.get("personal", {})
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
            "donna_managed, donna_task_id, etag, last_synced FROM calendar_mirror"
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
            await conn.execute(
                """
                INSERT INTO calendar_mirror
                    (event_id, calendar_id, summary, start_time, end_time,
                     donna_managed, donna_task_id, etag, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    calendar_id  = excluded.calendar_id,
                    summary      = excluded.summary,
                    start_time   = excluded.start_time,
                    end_time     = excluded.end_time,
                    donna_managed = excluded.donna_managed,
                    donna_task_id = excluded.donna_task_id,
                    etag         = excluded.etag,
                    last_synced  = excluded.last_synced
                """,
                (
                    ev.event_id,
                    ev.calendar_id,
                    ev.summary,
                    ev.start.isoformat(),
                    ev.end.isoformat(),
                    int(ev.donna_managed),
                    ev.donna_task_id,
                    ev.etag,
                    now_str,
                ),
            )

        # Remove rows no longer in live data (events deleted from calendar).
        if live_events:
            placeholders = ",".join("?" for _ in live_events)
            await conn.execute(
                f"DELETE FROM calendar_mirror WHERE event_id NOT IN ({placeholders})",
                list(live_events.keys()),
            )
        else:
            await conn.execute("DELETE FROM calendar_mirror")

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

    async def _log_correction(
        self,
        task_id: str,
        field: str,
        original: str,
        corrected: str,
    ) -> None:
        """Write a correction_log row for preference learning."""
        import uuid

        conn = self._db.connection
        await conn.execute(
            """
            INSERT INTO correction_log
                (id, timestamp, user_id, task_type, task_id, input_text,
                 field_corrected, original_value, corrected_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                datetime.utcnow().isoformat(),
                "nick",  # single-user Phase 1
                "calendar_sync",
                task_id,
                "calendar_event_time_change",
                field,
                original,
                corrected,
            ),
        )
        await conn.commit()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_dt_str(value: str | None) -> datetime:
    """Parse an ISO datetime string (from SQLite) to a UTC-aware datetime."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Return True if interval [a_start, a_end) overlaps [b_start, b_end)."""
    return a_start < b_end and a_end > b_start
