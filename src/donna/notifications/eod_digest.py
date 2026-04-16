"""End-of-day digest — weekday afternoon status summary via email and Discord.

Runs at 5:30 PM UTC weekdays (configurable via EmailConfig.digest).
Assembles tasks completed today, still-open tasks, and cost summary,
then posts to Discord #donna-digest and creates an email draft.

See slices/slice_08_email_corrections.md and docs/notifications.md.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog

from donna.notifications.digest import _next_fire_time, _parse_dt
from donna.notifications.service import CHANNEL_DIGEST, NOTIF_DIGEST, NotificationService

if TYPE_CHECKING:
    from donna.config import EmailConfig
    from donna.integrations.gmail import GmailClient
    from donna.tasks.database import Database

logger = structlog.get_logger()

EOD_HOUR = 17
EOD_MINUTE = 30


class EodDigest:
    """Generates and posts the weekday end-of-day digest.

    Sends a plain-text summary to Discord #donna-digest and creates an
    email draft via GmailClient if configured.

    Usage:
        eod = EodDigest(db, service, gmail, user_id, user_email, email_config)
        asyncio.create_task(eod.run())
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        gmail: GmailClient | None,
        user_id: str,
        user_email: str,
        email_config: EmailConfig,
    ) -> None:
        self._db = db
        self._service = service
        self._gmail = gmail
        self._user_id = user_id
        self._user_email = user_email
        self._config = email_config

    async def run(self) -> None:
        """Sleep until the next weekday 5:30 PM UTC, fire digest, repeat."""
        hour = self._config.digest.eod_hour
        minute = self._config.digest.eod_minute
        weekdays_only = self._config.digest.eod_weekdays_only

        logger.info(
            "eod_digest_started",
            fire_hour=hour,
            fire_minute=minute,
            weekdays_only=weekdays_only,
        )

        while True:
            now = datetime.now(tz=timezone.utc)
            next_fire = _next_eod_fire_time(now, hour, minute, weekdays_only)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "eod_digest_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self._fire(datetime.now(tz=timezone.utc))
            except Exception:
                logger.exception("eod_digest_fire_failed")

    async def _fire(self, now: datetime) -> None:
        """Assemble data and send end-of-day digest."""
        data = await self._assemble_data(now)
        text = self._render(data)

        # Post to Discord.
        await self._service.dispatch(
            notification_type=NOTIF_DIGEST,
            content=text,
            channel=CHANNEL_DIGEST,
            priority=5,
        )

        # Create email draft if configured.
        if self._gmail is not None and self._user_email:
            subject = f"End-of-Day Digest — {data['current_date']}"
            await self._service.dispatch_email(
                to=self._user_email,
                subject=subject,
                body=text,
                priority=5,
            )

        logger.info("eod_digest_sent", date=data["current_date"])

    async def _assemble_skill_system_data(self, now: datetime) -> dict[str, Any]:
        """Collect 24h skill-system changes for the digest."""
        since_iso = (now - timedelta(hours=24)).isoformat()
        conn = self._db.connection

        # Skills flagged for review today (transitions INTO flagged_for_review).
        cursor = await conn.execute(
            """
            SELECT t.skill_id, s.capability_name, t.reason, t.at
              FROM skill_state_transition t
              JOIN skill s ON t.skill_id = s.id
             WHERE t.to_state = 'flagged_for_review'
               AND t.at >= ?
             ORDER BY t.at DESC
            """,
            (since_iso,),
        )
        flagged_rows = await cursor.fetchall()
        flagged = [
            {"skill_id": r[0], "capability_name": r[1], "reason": r[2], "at": r[3]}
            for r in flagged_rows
        ]

        # Skills auto-drafted today (candidate status flipped to 'drafted' today).
        cursor = await conn.execute(
            """
            SELECT id, capability_name, expected_savings_usd, resolved_at
              FROM skill_candidate_report
             WHERE status = 'drafted' AND resolved_at >= ?
             ORDER BY resolved_at DESC
            """,
            (since_iso,),
        )
        drafted_rows = await cursor.fetchall()
        drafted = [
            {
                "candidate_id": r[0],
                "capability_name": r[1],
                "expected_monthly_savings_usd": r[2],
                "at": r[3],
            }
            for r in drafted_rows
        ]

        # Skills promoted (sandbox → shadow_primary OR shadow_primary → trusted).
        cursor = await conn.execute(
            """
            SELECT t.skill_id, s.capability_name, t.from_state, t.to_state, t.at
              FROM skill_state_transition t
              JOIN skill s ON t.skill_id = s.id
             WHERE t.at >= ?
               AND (
                 (t.from_state = 'sandbox' AND t.to_state = 'shadow_primary')
                 OR (t.from_state = 'shadow_primary' AND t.to_state = 'trusted')
               )
             ORDER BY t.at DESC
            """,
            (since_iso,),
        )
        promoted_rows = await cursor.fetchall()
        promoted = [
            {
                "skill_id": r[0],
                "capability_name": r[1],
                "from_state": r[2],
                "to_state": r[3],
                "at": r[4],
            }
            for r in promoted_rows
        ]

        # Skills demoted (trusted → flagged_for_review).
        cursor = await conn.execute(
            """
            SELECT t.skill_id, s.capability_name, t.at
              FROM skill_state_transition t
              JOIN skill s ON t.skill_id = s.id
             WHERE t.from_state = 'trusted'
               AND t.to_state = 'flagged_for_review'
               AND t.at >= ?
            """,
            (since_iso,),
        )
        demoted = [
            {"skill_id": r[0], "capability_name": r[1], "at": r[2]}
            for r in await cursor.fetchall()
        ]

        # Claude spend on skill-system work (auto-draft + shadow-eq-judge + triage).
        cursor = await conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)
              FROM invocation_log
             WHERE timestamp >= ?
               AND task_type IN ('skill_auto_draft', 'skill_equivalence_judge', 'triage_failure')
            """,
            (since_iso,),
        )
        row = await cursor.fetchone()
        skill_system_cost = float(row[0]) if row else 0.0

        return {
            "flagged": flagged,
            "drafted": drafted,
            "promoted": promoted,
            "demoted": demoted,
            "skill_system_cost_usd": skill_system_cost,
        }

    def _render_skill_section(self, skill_data: dict[str, Any]) -> str:
        """Render the skill-system changes section. Empty section if no activity."""
        if not skill_data:
            return ""

        lines = ["**Skill System Changes (last 24h)**"]

        if skill_data.get("drafted"):
            lines.append("")
            lines.append(f"Auto-drafted: {len(skill_data['drafted'])} skill(s)")
            for d in skill_data["drafted"][:5]:
                lines.append(
                    f"  - {d['capability_name']} "
                    f"(expected monthly savings ${d['expected_monthly_savings_usd']:.2f})"
                )

        if skill_data.get("promoted"):
            lines.append("")
            lines.append(f"Promoted: {len(skill_data['promoted'])} skill(s)")
            for p in skill_data["promoted"][:5]:
                lines.append(
                    f"  - {p['capability_name']} "
                    f"({p['from_state']} → {p['to_state']})"
                )

        if skill_data.get("demoted"):
            lines.append("")
            lines.append(f"Demoted to flagged_for_review: {len(skill_data['demoted'])} skill(s)")
            for d in skill_data["demoted"][:5]:
                lines.append(f"  - {d['capability_name']}")

        if skill_data.get("flagged"):
            lines.append("")
            lines.append(f"Flagged for review: {len(skill_data['flagged'])} skill(s)")
            for f in skill_data["flagged"][:5]:
                lines.append(f"  - {f['capability_name']} (reason: {f['reason']})")

        lines.append("")
        lines.append(f"Skill-system Claude spend: ${skill_data['skill_system_cost_usd']:.4f}")

        # If no activity at all, return a short stub.
        no_activity = not (
            skill_data.get("drafted") or skill_data.get("promoted")
            or skill_data.get("demoted") or skill_data.get("flagged")
        )
        if no_activity and skill_data.get("skill_system_cost_usd", 0.0) == 0.0:
            return "**Skill System Changes (last 24h)**\nNo changes in the last 24 hours."

        return "\n".join(lines)

    async def _assemble_data(self, now: datetime) -> dict[str, Any]:
        """Collect end-of-day task and cost data."""
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        all_tasks = await self._db.list_tasks(user_id=self._user_id)

        today_iso = today_start.date().isoformat()
        completed_today: list[str] = []
        still_open: list[str] = []

        for task in all_tasks:
            status = task.status
            if status == "cancelled":
                continue

            if status == "done":
                # Completed today.
                completed_at = getattr(task, "completed_at", None) or ""
                if completed_at and completed_at[:10] == today_iso:
                    completed_today.append(f"- {task.title} (priority {task.priority})")
            elif status not in ("done",):
                # Still open (not done, not cancelled).
                still_open.append(f"- {task.title} (status: {status}, priority {task.priority})")

        # Cost summary.
        today_cost = 0.0
        try:
            conn = self._db.connection
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
                (today_start.isoformat(),),
            )).fetchone()
            if row:
                today_cost = float(row[0])
        except Exception:
            logger.exception("eod_digest_cost_query_failed")

        skill_system = {}
        try:
            skill_system = await self._assemble_skill_system_data(now)
        except Exception:
            logger.exception("eod_digest_skill_system_query_failed")

        return {
            "current_date": today_start.strftime("%Y-%m-%d"),
            "day_of_week": today_start.strftime("%A"),
            "completed_today": "\n".join(completed_today) or "None.",
            "still_open": "\n".join(still_open) or "None.",
            "today_cost": f"{today_cost:.4f}",
            "skill_system": skill_system,
        }

    def _render(self, data: dict[str, Any]) -> str:
        """Render end-of-day digest as plain text."""
        lines = [
            f"**End-of-Day Digest — {data['day_of_week']}, {data['current_date']}**",
            "",
            "**Completed Today**",
            data["completed_today"],
            "",
            "**Still Open**",
            data["still_open"],
            "",
            "**Cost Today**",
            f"${data['today_cost']}",
        ]
        # Append skill-system section if data present.
        skill_section = self._render_skill_section(data.get("skill_system", {}))
        if skill_section:
            lines.append("")
            lines.append(skill_section)
        text = "\n".join(lines)
        return text[:2000]  # Discord message limit


def _next_eod_fire_time(
    now: datetime, hour: int, minute: int, weekdays_only: bool
) -> datetime:
    """Return the next EOD fire time (UTC), skipping weekends if configured."""
    candidate = _next_fire_time(now, hour, minute)

    if not weekdays_only:
        return candidate

    # Advance past weekends (Mon=0 … Fri=4, Sat=5, Sun=6).
    for _ in range(7):
        if candidate.weekday() < 5:
            return candidate
        candidate = _next_fire_time(candidate, hour, minute)

    return candidate
