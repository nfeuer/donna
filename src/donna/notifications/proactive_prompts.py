"""Proactive prompts — background loops that nudge the user at key moments.

Four prompt types:
  - PostMeetingCapture: After a calendar meeting ends, ask for action items.
  - EveningCheckin: At 7pm, ask if there's anything to capture for tomorrow.
  - StaleTaskDetector: Once daily, flag backlog tasks >7 days with no schedule.
  - AfternoonInactivityCheck: At 2pm, nudge if no tasks started/added/completed.

All follow the same sleep-until-fire-time pattern used by MorningDigest
and OverdueDetector.

See docs/notifications.md and the discord interaction expansion plan.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
import structlog

from donna.notifications.service import CHANNEL_TASKS, NotificationService
from donna.tasks.database import Database
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

EMBED_COLOUR = 0x5865F2


def _next_fire_time(now: datetime, hour: int, minute: int) -> datetime:
    """Calculate the next fire time for a daily prompt."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


# ------------------------------------------------------------------
# Post-Meeting Capture
# ------------------------------------------------------------------


class PostMeetingCapture:
    """Polls for recently-ended calendar meetings and prompts for action items.

    Checks every 5 minutes (configurable) for meetings that ended since
    the last check. Posts to #donna-tasks asking for any new tasks.
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        user_id: str,
        delay_minutes: int = 5,
    ) -> None:
        self._db = db
        self._service = service
        self._user_id = user_id
        self._delay_minutes = delay_minutes
        self._prompted_events: set[str] = set()

    async def run(self) -> None:
        """Loop forever, checking for ended meetings."""
        logger.info(
            "post_meeting_capture_started",
            delay_minutes=self._delay_minutes,
        )

        while True:
            try:
                await self._check_ended_meetings(datetime.now(tz=UTC))
            except Exception:
                logger.exception("post_meeting_capture_check_failed")

            await asyncio.sleep(self._delay_minutes * 60)

    async def _check_ended_meetings(self, now: datetime) -> None:
        """Query calendar_mirror for meetings ended in the last interval."""
        conn = self._db.connection
        cutoff = (now - timedelta(minutes=self._delay_minutes)).isoformat()
        now_iso = now.isoformat()

        cursor = await conn.execute(
            "SELECT event_id, summary FROM calendar_mirror "
            "WHERE end_time BETWEEN ? AND ? AND user_id = ? AND donna_managed = 0",
            (cutoff, now_iso, self._user_id),
        )
        rows = await cursor.fetchall()

        for event_id, summary in rows:
            if event_id in self._prompted_events:
                continue
            self._prompted_events.add(event_id)

            meeting_name = summary or "your meeting"
            embed = discord.Embed(
                title="Post-Meeting Capture",
                description=(
                    f"**{meeting_name}** just ended.\n"
                    "Any new tasks or action items?"
                ),
                colour=EMBED_COLOUR,
            )
            await self._service.dispatch(
                notification_type="post_meeting",
                content=f"Post-meeting capture: {meeting_name}",
                channel=CHANNEL_TASKS,
                priority=3,
                embed=embed,
            )
            logger.info(
                "post_meeting_capture_prompted",
                event_id=event_id,
                summary=summary,
            )


# ------------------------------------------------------------------
# Evening Check-in
# ------------------------------------------------------------------


class EveningCheckin:
    """Fires at a configurable time (default 7pm) for end-of-day capture."""

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        user_id: str,
        hour: int = 19,
        minute: int = 0,
    ) -> None:
        self._db = db
        self._service = service
        self._user_id = user_id
        self._hour = hour
        self._minute = minute

    async def run(self) -> None:
        """Sleep until fire time, post check-in, repeat."""
        logger.info(
            "evening_checkin_started",
            hour=self._hour,
            minute=self._minute,
        )

        while True:
            now = datetime.now(tz=UTC)
            next_fire = _next_fire_time(now, self._hour, self._minute)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "evening_checkin_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self._fire()
            except Exception:
                logger.exception("evening_checkin_fire_failed")

    async def _fire(self) -> None:
        """Post the evening check-in message."""
        # Preview tomorrow's first task.
        tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
        tasks = await self._db.list_tasks(user_id=self._user_id)
        tomorrow_tasks = [
            t for t in tasks
            if t.scheduled_start and t.scheduled_start[:10] == tomorrow
            and t.status not in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value)
        ]
        tomorrow_tasks.sort(key=lambda t: t.scheduled_start or "")

        preview = ""
        if tomorrow_tasks:
            first = tomorrow_tasks[0]
            time_str = (
                first.scheduled_start[11:16]
                if first.scheduled_start and len(first.scheduled_start) > 16
                else ""
            )
            preview = f"\n\nTomorrow's first task: **{first.title}** at {time_str}"

        embed = discord.Embed(
            title="Evening Check-in",
            description=f"Anything to capture before tomorrow?{preview}",
            colour=EMBED_COLOUR,
        )
        await self._service.dispatch(
            notification_type="evening_checkin",
            content="Evening check-in",
            channel=CHANNEL_TASKS,
            priority=3,
            embed=embed,
        )
        logger.info("evening_checkin_sent")


# ------------------------------------------------------------------
# Stale Task Detector
# ------------------------------------------------------------------


class StaleTaskDetector:
    """Checks for backlog tasks >N days old with no scheduled time."""

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        user_id: str,
        stale_days: int = 7,
        check_interval_hours: int = 24,
    ) -> None:
        self._db = db
        self._service = service
        self._user_id = user_id
        self._stale_days = stale_days
        self._check_interval_hours = check_interval_hours

    async def run(self) -> None:
        """Loop forever, checking for stale tasks."""
        logger.info(
            "stale_task_detector_started",
            stale_days=self._stale_days,
            interval_hours=self._check_interval_hours,
        )

        while True:
            try:
                await self._check()
            except Exception:
                logger.exception("stale_task_check_failed")

            await asyncio.sleep(self._check_interval_hours * 3600)

    async def _check(self) -> None:
        """Find and flag stale tasks."""
        cutoff = (
            datetime.now(tz=UTC) - timedelta(days=self._stale_days)
        ).isoformat()

        tasks = await self._db.list_tasks(
            user_id=self._user_id, status=TaskStatus.BACKLOG
        )
        stale = [
            t for t in tasks
            if t.created_at < cutoff and not t.scheduled_start
        ]

        for t in stale:
            embed = discord.Embed(
                title="Stale Task Detected",
                description=(
                    f"**{t.title}** has been in backlog for "
                    f"{self._stale_days}+ days with no scheduled time.\n\n"
                    "Schedule it or archive it?"
                ),
                colour=0xF39C12,  # Orange/warning
            )
            embed.add_field(name="Created", value=t.created_at[:10])
            embed.add_field(name="Priority", value=str(t.priority))
            embed.add_field(name="Domain", value=t.domain)

            await self._service.dispatch(
                notification_type="stale_task",
                content=f"Stale task: {t.title}",
                channel=CHANNEL_TASKS,
                priority=2,
                embed=embed,
            )
            logger.info(
                "stale_task_flagged",
                task_id=t.id,
                title=t.title,
                age_days=self._stale_days,
            )


# ------------------------------------------------------------------
# Afternoon Inactivity Check
# ------------------------------------------------------------------


class AfternoonInactivityCheck:
    """Fires at 2pm (configurable) if no tasks were started, added, or completed today.

    Helps catch days when the user is busy and forgets to update Donna.
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        user_id: str,
        hour: int = 14,
        minute: int = 0,
    ) -> None:
        self._db = db
        self._service = service
        self._user_id = user_id
        self._hour = hour
        self._minute = minute

    async def run(self) -> None:
        """Sleep until fire time, check activity, repeat."""
        logger.info(
            "afternoon_inactivity_started",
            hour=self._hour,
            minute=self._minute,
        )

        while True:
            now = datetime.now(tz=UTC)
            next_fire = _next_fire_time(now, self._hour, self._minute)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "afternoon_inactivity_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self._fire()
            except Exception:
                logger.exception("afternoon_inactivity_fire_failed")

    async def _fire(self) -> None:
        """Check for today's activity and nudge if none found."""
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

        tasks = await self._db.list_tasks(user_id=self._user_id)

        has_activity = False
        for t in tasks:
            # Created today?
            if t.created_at[:10] == today:
                has_activity = True
                break
            # Completed today?
            if t.completed_at and t.completed_at[:10] == today:
                has_activity = True
                break
            # Started today (actual_start)?
            if t.actual_start and t.actual_start[:10] == today:
                has_activity = True
                break
            # In progress with today's scheduled_start?
            if (
                t.status == TaskStatus.IN_PROGRESS.value
                and t.scheduled_start
                and t.scheduled_start[:10] == today
            ):
                has_activity = True
                break

        if has_activity:
            logger.info("afternoon_inactivity_activity_found")
            return

        embed = discord.Embed(
            title="Afternoon Check-in",
            description=(
                "No tasks started, added, or completed today so far.\n"
                "Any updates? Capture something quick or let me know you're all good."
            ),
            colour=0xF39C12,
        )
        await self._service.dispatch(
            notification_type="afternoon_inactivity",
            content="Afternoon inactivity check",
            channel=CHANNEL_TASKS,
            priority=3,
            embed=embed,
        )
        logger.info("afternoon_inactivity_nudge_sent")
