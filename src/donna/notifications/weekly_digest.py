"""Weekly efficiency digest — productivity insights and patterns.

Fires every Sunday at 7 PM. Assembles task completion stats, nudge
frequency, reschedule counts, and domain breakdowns, then generates
a Donna-voiced summary via the local LLM.

Degraded mode: if the LLM call fails, posts a plain-text stats table.

See docs/notifications.md.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
import structlog

from donna.models.router import ContextOverflowError, ModelRouter
from donna.notifications.service import CHANNEL_DIGEST, NOTIF_DIGEST, NotificationService
from donna.tasks.database import Database

logger = structlog.get_logger()

WEEKLY_FIRE_WEEKDAY = 6  # Sunday (0=Monday in isoweekday, 6=Sunday)
WEEKLY_FIRE_HOUR = 19  # 7 PM UTC
WEEKLY_FIRE_MINUTE = 0
EMBED_COLOUR = 0xE67E22  # Orange


class WeeklyDigest:
    """Generates and posts the weekly efficiency digest.

    Usage:
        digest = WeeklyDigest(db, service, router, user_id)
        asyncio.create_task(digest.run())
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        router: ModelRouter,
        user_id: str,
    ) -> None:
        self._db = db
        self._service = service
        self._router = router
        self._user_id = user_id

    async def run(self) -> None:
        """Sleep until next Sunday 7 PM, fire digest, repeat."""
        logger.info(
            "weekly_digest_started",
            fire_weekday=WEEKLY_FIRE_WEEKDAY,
            fire_hour=WEEKLY_FIRE_HOUR,
        )

        while True:
            now = datetime.now(tz=UTC)
            next_fire = _next_sunday_fire(now)
            wait_seconds = (next_fire - now).total_seconds()

            logger.info(
                "weekly_digest_waiting",
                next_fire=next_fire.isoformat(),
                wait_seconds=int(wait_seconds),
            )
            await asyncio.sleep(max(wait_seconds, 0))

            try:
                await self._fire()
            except Exception:
                logger.exception("weekly_digest_fire_failed")

    async def _fire(self) -> None:
        """Assemble stats, generate digest, and post."""
        since = datetime.now(tz=UTC) - timedelta(days=7)
        stats = await self._db.get_weekly_stats(self._user_id, since)

        # Try LLM-generated digest.
        digest_text: str | None = None
        try:
            prompt = self._build_prompt(stats)
            result, _ = await self._router.complete(
                prompt, task_type="generate_weekly_digest", user_id=self._user_id
            )
            digest_text = result.get("digest_text") if isinstance(result, dict) else None
        except ContextOverflowError:
            raise
        except Exception:
            logger.exception("weekly_digest_llm_failed")

        if digest_text:
            embed = discord.Embed(
                title="Weekly Efficiency Report",
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
            logger.info("weekly_digest_sent_llm")
        else:
            fallback = self._render_fallback(stats)
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=fallback,
                channel=CHANNEL_DIGEST,
                priority=5,
            )
            logger.info("weekly_digest_sent_fallback")

    def _build_prompt(self, stats: dict[str, Any]) -> str:
        """Render the weekly digest prompt with stats context."""
        most_nudged_str = "\n".join(
            f"  - {t['title']} ({t['nudge_count']} nudges, {t['domain']})"
            for t in stats.get("most_nudged", [])
        ) or "  None"

        most_rescheduled_str = "\n".join(
            f"  - {t['title']} ({t['reschedule_count']} reschedules, {t['domain']})"
            for t in stats.get("most_rescheduled", [])
        ) or "  None"

        domain_str = "\n".join(
            f"  - {d}: {v['completed']} completed, {v['open']} open, avg {v['avg_nudges']} nudges"
            for d, v in stats.get("domain_breakdown", {}).items()
        ) or "  No data"

        return f"""You are Donna. Generate a weekly efficiency report for Nick.

Stats this week:
- Tasks completed: {stats.get('tasks_completed', 0)} / {stats.get('tasks_created', 0)} created
- Completion rate: {stats.get('completion_rate', 0)}%
- Average time to complete: {stats.get('avg_hours_to_complete', 'N/A')} hours
- Total nudges sent: {stats.get('total_nudges', 0)}
- LLM cost this week: ${stats.get('weekly_cost', 0):.2f}

Most nudged tasks:
{most_nudged_str}

Most rescheduled tasks:
{most_rescheduled_str}

Domain breakdown:
{domain_str}

Provide:
1. A 2-3 sentence summary of the week
2. One specific pattern you noticed (positive or negative)
3. One actionable suggestion for next week

Be direct, no fluff. Use Donna's voice — confident, sharp, efficient.

Respond with JSON:
{{
  "digest_text": "The full weekly report text"
}}"""

    def _render_fallback(self, stats: dict[str, Any]) -> str:
        """Plain-text fallback when the LLM is unavailable."""
        lines = [
            "**Weekly Efficiency Report**",
            "",
            (
                f"Tasks: {stats.get('tasks_completed', 0)} completed / "
                f"{stats.get('tasks_created', 0)} created "
                f"({stats.get('completion_rate', 0)}% rate)"
            ),
        ]

        avg = stats.get("avg_hours_to_complete")
        if avg is not None:
            lines.append(f"Avg time to complete: {avg:.1f} hours")

        lines.append(f"Total nudges: {stats.get('total_nudges', 0)}")
        lines.append(f"LLM cost: ${stats.get('weekly_cost', 0):.2f}")

        if stats.get("most_nudged"):
            lines.append("")
            lines.append("**Most nudged:**")
            for t in stats["most_nudged"][:3]:
                lines.append(f"  • {t['title']} — {t['nudge_count']} nudges")

        if stats.get("most_rescheduled"):
            lines.append("")
            lines.append("**Most rescheduled:**")
            for t in stats["most_rescheduled"][:3]:
                lines.append(f"  • {t['title']} — {t['reschedule_count']} times")

        return "\n".join(lines)


def _next_sunday_fire(now: datetime) -> datetime:
    """Calculate the next Sunday at WEEKLY_FIRE_HOUR:WEEKLY_FIRE_MINUTE UTC."""
    # isoweekday: Monday=1, Sunday=7
    days_until_sunday = (7 - now.isoweekday()) % 7
    target = now.replace(
        hour=WEEKLY_FIRE_HOUR,
        minute=WEEKLY_FIRE_MINUTE,
        second=0,
        microsecond=0,
    ) + timedelta(days=days_until_sunday)

    # If we're past the fire time on Sunday, wait until next week.
    if target <= now:
        target += timedelta(days=7)

    return target
