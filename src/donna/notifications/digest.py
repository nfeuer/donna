"""Morning digest — daily proactive schedule and status summary.

Runs at 6:30 AM daily (configurable via DIGEST_HOUR / DIGEST_MINUTE).
Assembles a data payload from the database and calendar, renders the
prompts/morning_digest.md Jinja2 template, calls the LLM via ModelRouter,
and posts the result to Discord #donna-digest as an embed.

Degraded mode: if the LLM call fails, the same template is rendered
with raw data and posted as a plain-text message.

See slices/slice_05_reminders_digest.md and docs/notifications.md.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
import structlog

from donna.integrations.calendar import GoogleCalendarClient
from donna.models.router import ModelRouter
from donna.notifications.service import CHANNEL_DIGEST, NOTIF_DIGEST, NotificationService
from donna.tasks.database import Database

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from donna.integrations.gmail import GmailClient
    from donna.resilience.health_check import SelfDiagnostic

logger = structlog.get_logger()

DIGEST_HOUR = 6
DIGEST_MINUTE = 30
OVERDUE_BUFFER_MINUTES = 30
EMBED_COLOUR = 0x5865F2  # Discord blurple


class MorningDigest:
    """Generates and posts the daily morning digest.

    Usage:
        digest = MorningDigest(db, service, router, calendar_client, calendar_id,
                               user_id, project_root)
        asyncio.create_task(digest.run())
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        router: ModelRouter,
        calendar_client: GoogleCalendarClient,
        calendar_id: str,
        user_id: str,
        project_root: Path,
        gmail: GmailClient | None = None,
        user_email: str = "",
        self_diagnostic: SelfDiagnostic | None = None,
    ) -> None:
        self._db = db
        self._service = service
        self._router = router
        self._calendar_client = calendar_client
        self._calendar_id = calendar_id
        self._user_id = user_id
        self._project_root = project_root
        self._gmail = gmail
        self._user_email = user_email
        self._self_diagnostic = self_diagnostic

    async def run(self) -> None:
        """Sleep until the next 6:30 AM, fire digest, repeat."""
        logger.info(
            "morning_digest_started",
            fire_hour=DIGEST_HOUR,
            fire_minute=DIGEST_MINUTE,
        )

        while True:
            now = datetime.now(tz=timezone.utc)
            next_fire = _next_fire_time(now, DIGEST_HOUR, DIGEST_MINUTE)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "morning_digest_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self._fire(datetime.now(tz=timezone.utc))
            except Exception:
                logger.exception("morning_digest_fire_failed")

    async def _fire(self, now: datetime) -> None:
        """Assemble data, render template, post digest."""
        data = await self._assemble_data(now)

        # Layer 3: prepend self-diagnostic warnings to system_status.
        if self._self_diagnostic is not None:
            try:
                issues = await self._self_diagnostic.run()
                if issues:
                    data["system_status"] = "\n".join(
                        [":warning: System warnings:"] + [f"  • {w}" for w in issues]
                    )
            except Exception:
                logger.exception("morning_digest_self_diagnostic_failed")

        template_text = (self._project_root / "prompts" / "morning_digest.md").read_text()

        # Attempt LLM-generated digest.
        digest_text: str | None = None
        try:
            rendered_prompt = _render_template(template_text, data)
            result, _ = await self._router.complete(rendered_prompt, task_type="generate_digest")
            digest_text = result.get("digest_text") if isinstance(result, dict) else None
        except Exception:
            logger.exception("morning_digest_llm_failed")

        if digest_text:
            embed = discord.Embed(
                title=f"Good morning — {data['day_of_week']}, {data['current_date']}",
                description=digest_text,
                colour=EMBED_COLOUR,
            )
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=digest_text,
                channel=CHANNEL_DIGEST,
                priority=5,
                embed=embed,
            )
            logger.info("morning_digest_sent_llm")
            email_body = digest_text
        else:
            # Degraded mode: plain text from raw data.
            fallback_text = self._render_degraded(data)
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=fallback_text,
                channel=CHANNEL_DIGEST,
                priority=5,
            )
            logger.info("morning_digest_sent_degraded")
            email_body = fallback_text

        # Also create an email draft if Gmail is configured.
        if self._gmail is not None and self._user_email:
            subject = f"Morning Digest — {data['day_of_week']}, {data['current_date']}"
            await self._service.dispatch_email(
                to=self._user_email,
                subject=subject,
                body=email_body,
                priority=5,
            )

    async def _assemble_data(self, now: datetime) -> dict[str, Any]:
        """Collect all data needed for the digest template."""
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        yesterday_start = today_start - timedelta(days=1)

        # Calendar events for today.
        calendar_events_list: list[str] = []
        try:
            events = await self._calendar_client.list_events(
                self._calendar_id, today_start, today_end
            )
            calendar_events_list = [
                f"- {ev.summary} ({ev.start.strftime('%H:%M')}–{ev.end.strftime('%H:%M')})"
                for ev in events
            ]
        except Exception:
            logger.exception("morning_digest_calendar_failed")

        all_tasks = await self._db.list_tasks(user_id=self._user_id)

        today_iso = today_start.date().isoformat()
        yesterday_iso = yesterday_start.date().isoformat()

        tasks_due_today: list[str] = []
        carryover_tasks: list[str] = []
        overdue_tasks: list[str] = []

        for task in all_tasks:
            status = task.status
            if status in ("done", "cancelled"):
                continue

            # Tasks due today.
            if task.deadline:
                dl = task.deadline[:10]
                if dl == today_iso:
                    tasks_due_today.append(f"- {task.title} (priority {task.priority})")

            # Carryover: scheduled yesterday, not completed.
            if task.scheduled_start:
                sched_date = task.scheduled_start[:10]
                if sched_date == yesterday_iso:
                    carryover_tasks.append(f"- {task.title} (status: {task.status})")

            # Overdue: past estimated end + buffer.
            if task.scheduled_start:
                start = _parse_dt(task.scheduled_start)
                if start:
                    duration_min = task.estimated_duration or 0
                    overdue_at = start + timedelta(minutes=duration_min + OVERDUE_BUFFER_MINUTES)
                    if now > overdue_at:
                        overdue_tasks.append(f"- {task.title} (overdue since {overdue_at.strftime('%Y-%m-%d %H:%M')})")

        # Cost summary from invocation_log.
        yesterday_cost = 0.0
        mtd_cost = 0.0
        try:
            conn = self._db.connection
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
                (yesterday_start.isoformat(),),
            )).fetchone()
            if row:
                yesterday_cost = float(row[0])

            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            row2 = await (await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
                (month_start.isoformat(),),
            )).fetchone()
            if row2:
                mtd_cost = float(row2[0])
        except Exception:
            logger.exception("morning_digest_cost_query_failed")

        monthly_budget = 100.0
        try:
            monthly_budget = self._router._models_config.cost.monthly_budget_usd
        except Exception:
            pass

        return {
            "current_date": today_start.strftime("%Y-%m-%d"),
            "day_of_week": today_start.strftime("%A"),
            "calendar_events": "\n".join(calendar_events_list) or "No events today.",
            "tasks_due_today": "\n".join(tasks_due_today) or "None.",
            "carryover_tasks": "\n".join(carryover_tasks) or "None.",
            "overdue_tasks": "\n".join(overdue_tasks) or "None.",
            "prep_work_results": "No prep work completed.",
            "agent_activity": "No agent activity since last digest.",
            "system_status": "All systems normal.",
            "yesterday_cost": f"{yesterday_cost:.4f}",
            "mtd_cost": f"{mtd_cost:.4f}",
            "monthly_budget": f"{monthly_budget:.2f}",
        }

    def _render_degraded(self, data: dict[str, Any]) -> str:
        """Render a plain-text digest when the LLM is unavailable."""
        lines = [
            f"**Morning Digest — {data['day_of_week']}, {data['current_date']}**",
            "",
            "**Calendar Events**",
            data["calendar_events"],
            "",
            "**Tasks Due Today**",
            data["tasks_due_today"],
            "",
            "**Carry-over Tasks**",
            data["carryover_tasks"],
            "",
            "**Overdue Tasks**",
            data["overdue_tasks"],
            "",
            "**Cost Summary**",
            f"Yesterday: ${data['yesterday_cost']} | Month-to-date: ${data['mtd_cost']} / ${data['monthly_budget']}",
        ]
        text = "\n".join(lines)
        # Discord message limit.
        return text[:2000]


def _next_fire_time(now: datetime, hour: int, minute: int) -> datetime:
    """Return the next datetime at hour:minute (UTC), at least 1 second away."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _render_template(template_text: str, data: dict[str, Any]) -> str:
    """Render a Jinja2-style template with the given data dict."""
    try:
        from jinja2 import Template
        return Template(template_text).render(**data)
    except ImportError:
        # Fallback: naive string replacement for {{ key }} patterns.
        result = template_text
        for key, value in data.items():
            result = result.replace("{{ " + key + " }}", str(value))
            result = result.replace("{{" + key + "}}", str(value))
        return result


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
