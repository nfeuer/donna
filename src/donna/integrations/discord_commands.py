"""Discord slash commands for direct task management.

Provides /tasks, /done, /cancel, /reschedule, /next, /today, /tomorrow,
/edit, and /status commands. Uses guild-specific registration for instant
sync. All commands use autocomplete for task_id selection.

See the discord interaction expansion plan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
import structlog
from discord import Interaction, app_commands

from donna.integrations.discord_views import (
    TaskEditModal,
    TaskListPaginationView,
)
from donna.tasks.database import Database
from donna.tasks.db_models import TaskDomain, TaskStatus

if TYPE_CHECKING:
    from donna.integrations.discord_bot import DonnaBot

logger = structlog.get_logger()

EMBED_COLOUR = 0x5865F2

# Statuses considered "active" for listing and autocomplete.
_ACTIVE_STATUSES = {
    TaskStatus.BACKLOG,
    TaskStatus.SCHEDULED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
    TaskStatus.WAITING_INPUT,
}


async def _task_autocomplete(
    interaction: Interaction,
    current: str,
    db: Database,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for task_id: show recent active tasks matching input."""
    tasks = await db.list_tasks()
    active = [t for t in tasks if t.status in {s.value for s in _ACTIVE_STATUSES}]
    choices = []
    for t in active[:25]:
        label = f"{t.title[:80]} ({t.id[:8]})"
        if current.lower() in label.lower() or current.lower() in t.id.lower():
            choices.append(app_commands.Choice(name=label[:100], value=t.id))
        if len(choices) >= 25:
            break
    return choices


def register_commands(bot: DonnaBot, db: Database, user_id: str) -> None:
    """Register all slash commands on the bot's command tree."""

    guild = discord.Object(id=bot._guild_id) if bot._guild_id else None

    # ------------------------------------------------------------------
    # /tasks
    # ------------------------------------------------------------------
    @bot.tree.command(name="tasks", description="List your tasks", guild=guild)
    @app_commands.describe(
        status="Filter by status (backlog, scheduled, in_progress, etc.)",
        domain="Filter by domain (personal, work, family)",
    )
    async def list_tasks_cmd(
        interaction: Interaction,
        status: str | None = None,
        domain: str | None = None,
    ) -> None:
        status_filter = None
        if status:
            try:
                status_filter = TaskStatus(status.lower())
            except ValueError:
                await interaction.response.send_message(
                    f"Unknown status: {status}", ephemeral=True
                )
                return

        domain_filter = None
        if domain:
            try:
                domain_filter = TaskDomain(domain.lower())
            except ValueError:
                await interaction.response.send_message(
                    f"Unknown domain: {domain}", ephemeral=True
                )
                return

        tasks = await db.list_tasks(
            user_id=user_id, status=status_filter, domain=domain_filter
        )

        if not tasks:
            await interaction.response.send_message("No tasks found.", ephemeral=True)
            return

        view = TaskListPaginationView(tasks=tasks, page=0, db=db)
        title = "Your Tasks"
        if status_filter:
            title += f" [{status_filter.value}]"
        if domain_filter:
            title += f" [{domain_filter.value}]"
        embed = view.build_embed(title=title)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        logger.info("slash_tasks", user=str(interaction.user), count=len(tasks))

    # ------------------------------------------------------------------
    # /done
    # ------------------------------------------------------------------
    @bot.tree.command(name="done", description="Mark a task as done", guild=guild)
    @app_commands.describe(task_id="The task to mark as done")
    async def done_cmd(interaction: Interaction, task_id: str) -> None:
        task = await db.get_task(task_id)
        if task is None:
            await interaction.response.send_message("Task not found.", ephemeral=True)
            return
        try:
            await db.update_task(task_id, status=TaskStatus.DONE)
            await interaction.response.send_message(
                f"'{task.title}' marked as done.", ephemeral=True
            )
            logger.info("slash_done", task_id=task_id)
        except Exception:
            logger.exception("slash_done_failed", task_id=task_id)
            await interaction.response.send_message(
                "Failed to update task.", ephemeral=True
            )

    @done_cmd.autocomplete("task_id")
    async def done_autocomplete(
        interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _task_autocomplete(interaction, current, db)

    # ------------------------------------------------------------------
    # /cancel
    # ------------------------------------------------------------------
    @bot.tree.command(name="cancel", description="Cancel a task", guild=guild)
    @app_commands.describe(task_id="The task to cancel")
    async def cancel_cmd(interaction: Interaction, task_id: str) -> None:
        task = await db.get_task(task_id)
        if task is None:
            await interaction.response.send_message("Task not found.", ephemeral=True)
            return
        try:
            await db.update_task(task_id, status=TaskStatus.CANCELLED)
            await interaction.response.send_message(
                f"'{task.title}' cancelled.", ephemeral=True
            )
            logger.info("slash_cancel", task_id=task_id)
        except Exception:
            logger.exception("slash_cancel_failed", task_id=task_id)
            await interaction.response.send_message(
                "Failed to cancel task.", ephemeral=True
            )

    @cancel_cmd.autocomplete("task_id")
    async def cancel_autocomplete(
        interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _task_autocomplete(interaction, current, db)

    # ------------------------------------------------------------------
    # /reschedule
    # ------------------------------------------------------------------
    @bot.tree.command(name="reschedule", description="Reschedule a task", guild=guild)
    @app_commands.describe(
        task_id="The task to reschedule",
        when="When to reschedule to (e.g. 'tomorrow 2pm', '2024-04-05 14:00')",
    )
    async def reschedule_cmd(
        interaction: Interaction, task_id: str, when: str
    ) -> None:
        task = await db.get_task(task_id)
        if task is None:
            await interaction.response.send_message("Task not found.", ephemeral=True)
            return

        new_start = _parse_when(when)
        if new_start is None:
            await interaction.response.send_message(
                f"Couldn't parse time: '{when}'. Try 'tomorrow 2pm' or an ISO date.",
                ephemeral=True,
            )
            return

        try:
            reschedule_count = task.reschedule_count + 1
            await db.update_task(
                task_id,
                scheduled_start=new_start.isoformat(),
                status=TaskStatus.SCHEDULED,
                reschedule_count=reschedule_count,
            )
            await interaction.response.send_message(
                f"'{task.title}' rescheduled to {new_start.strftime('%Y-%m-%d %H:%M')}.",
                ephemeral=True,
            )
            logger.info("slash_reschedule", task_id=task_id, new_start=new_start.isoformat())
        except Exception:
            logger.exception("slash_reschedule_failed", task_id=task_id)
            await interaction.response.send_message(
                "Failed to reschedule.", ephemeral=True
            )

    @reschedule_cmd.autocomplete("task_id")
    async def reschedule_autocomplete(
        interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _task_autocomplete(interaction, current, db)

    # ------------------------------------------------------------------
    # /next
    # ------------------------------------------------------------------
    @bot.tree.command(name="next", description="Show your next scheduled task", guild=guild)
    async def next_cmd(interaction: Interaction) -> None:
        tasks = await db.list_tasks(user_id=user_id)
        now = datetime.now(tz=UTC).isoformat()
        scheduled = [
            t for t in tasks
            if t.scheduled_start and t.scheduled_start > now
            and t.status in {TaskStatus.SCHEDULED.value, TaskStatus.IN_PROGRESS.value}
        ]
        scheduled.sort(key=lambda t: t.scheduled_start or "")

        if not scheduled:
            await interaction.response.send_message(
                "No upcoming scheduled tasks.", ephemeral=True
            )
            return

        t = scheduled[0]
        embed = discord.Embed(
            title="Next Up",
            description=f"**{t.title}**",
            colour=EMBED_COLOUR,
        )
        embed.add_field(name="Scheduled", value=t.scheduled_start[:16] if t.scheduled_start else "N/A")
        embed.add_field(name="Priority", value=str(t.priority))
        embed.add_field(name="Domain", value=t.domain)
        if t.estimated_duration:
            embed.add_field(name="Duration", value=f"{t.estimated_duration} min")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("slash_next", task_id=t.id)

    # ------------------------------------------------------------------
    # /today
    # ------------------------------------------------------------------
    @bot.tree.command(name="today", description="Show today's schedule", guild=guild)
    async def today_cmd(interaction: Interaction) -> None:
        await _schedule_for_date(interaction, db, user_id, offset_days=0, label="Today")

    # ------------------------------------------------------------------
    # /tomorrow
    # ------------------------------------------------------------------
    @bot.tree.command(name="tomorrow", description="Show tomorrow's schedule", guild=guild)
    async def tomorrow_cmd(interaction: Interaction) -> None:
        await _schedule_for_date(interaction, db, user_id, offset_days=1, label="Tomorrow")

    # ------------------------------------------------------------------
    # /edit
    # ------------------------------------------------------------------
    @bot.tree.command(name="edit", description="Edit a task's details", guild=guild)
    @app_commands.describe(task_id="The task to edit")
    async def edit_cmd(interaction: Interaction, task_id: str) -> None:
        task = await db.get_task(task_id)
        if task is None:
            await interaction.response.send_message("Task not found.", ephemeral=True)
            return

        import json

        notes_str = ""
        if task.notes:
            try:
                notes_list = json.loads(task.notes)
                notes_str = "\n".join(notes_list) if isinstance(notes_list, list) else task.notes
            except (json.JSONDecodeError, TypeError):
                notes_str = task.notes

        modal = TaskEditModal(
            task_id=task_id,
            db=db,
            current_title=task.title,
            current_description=task.description or "",
            current_notes=notes_str,
            current_priority=str(task.priority),
            current_domain=task.domain,
        )
        await interaction.response.send_modal(modal)
        logger.info("slash_edit", task_id=task_id)

    @edit_cmd.autocomplete("task_id")
    async def edit_autocomplete(
        interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _task_autocomplete(interaction, current, db)

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------
    @bot.tree.command(name="status", description="Show system status", guild=guild)
    async def status_cmd(interaction: Interaction) -> None:
        tasks = await db.list_tasks(user_id=user_id)

        counts: dict[str, int] = {}
        for t in tasks:
            counts[t.status] = counts.get(t.status, 0) + 1

        total = len(tasks)
        active = sum(
            c for s, c in counts.items()
            if s not in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value)
        )

        embed = discord.Embed(title="Donna Status", colour=EMBED_COLOUR)
        embed.add_field(name="Total Tasks", value=str(total))
        embed.add_field(name="Active", value=str(active))
        embed.add_field(name="Done", value=str(counts.get("done", 0)))

        status_lines = [f"{s}: {c}" for s, c in sorted(counts.items())]
        embed.add_field(
            name="Breakdown", value="\n".join(status_lines) or "None", inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("slash_status", total=total, active=active)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _schedule_for_date(
    interaction: Interaction,
    db: Database,
    user_id: str,
    offset_days: int,
    label: str,
) -> None:
    """Show tasks scheduled for a specific date."""
    now = datetime.now(tz=UTC)
    target = now + timedelta(days=offset_days)
    target_date = target.strftime("%Y-%m-%d")

    tasks = await db.list_tasks(user_id=user_id)
    day_tasks = [
        t for t in tasks
        if t.scheduled_start and t.scheduled_start[:10] == target_date
        and t.status not in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value)
    ]
    day_tasks.sort(key=lambda t: t.scheduled_start or "")

    if not day_tasks:
        await interaction.response.send_message(
            f"No tasks scheduled for {label.lower()} ({target_date}).", ephemeral=True
        )
        return

    lines = []
    for t in day_tasks:
        time_str = t.scheduled_start[11:16] if t.scheduled_start and len(t.scheduled_start) > 16 else "??:??"
        duration = f" ({t.estimated_duration}min)" if t.estimated_duration else ""
        lines.append(f"**{time_str}** — {t.title} [P{t.priority}]{duration}")

    embed = discord.Embed(
        title=f"{label}'s Schedule — {target_date}",
        description="\n".join(lines),
        colour=EMBED_COLOUR,
    )
    embed.set_footer(text=f"{len(day_tasks)} task(s)")
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info(f"slash_{label.lower()}", count=len(day_tasks))


def _parse_when(text: str) -> datetime | None:
    """Parse a human-friendly time expression into a datetime.

    Supports:
      - ISO format: '2024-04-05T14:00', '2024-04-05 14:00'
      - 'tomorrow Xpm/am': 'tomorrow 2pm', 'tomorrow 10am'
      - 'today Xpm/am'
      - Relative: '+2h', '+30m'
    """
    text = text.strip()

    # ISO format
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    now = datetime.now(tz=UTC)

    # Relative: +2h, +30m
    import re

    rel_match = re.match(r"\+(\d+)([hm])", text.lower())
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2)
        delta = timedelta(hours=amount) if unit == "h" else timedelta(minutes=amount)
        return now + delta

    # "tomorrow 2pm", "today 10am"
    day_time_match = re.match(
        r"(today|tomorrow)\s+(\d{1,2})\s*(am|pm)?", text.lower()
    )
    if day_time_match:
        day_word = day_time_match.group(1)
        hour = int(day_time_match.group(2))
        ampm = day_time_match.group(3)

        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        base = now if day_word == "today" else now + timedelta(days=1)
        return base.replace(hour=hour, minute=0, second=0, microsecond=0)

    return None
