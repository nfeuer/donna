"""Discord slash commands for direct task management.

Provides /tasks, /done, /cancel, /reschedule, /next, /today, /tomorrow,
/edit, /status, and /breakdown commands. /breakdown (§7.2 resolution R2)
runs task decomposition via DecompositionService and is registered only
when that service is injected. Uses guild-specific registration for instant
sync; task-targeting commands use autocomplete for task_id selection.

See the discord interaction expansion plan.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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
    from donna.agents.decomposition import DecomposeResult, DecompositionService
    from donna.integrations.discord_bot import DonnaBot
    from donna.tasks.database import TaskRow

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


def register_commands(
    bot: DonnaBot,
    db: Database,
    user_id: str,
    calendar_client: Any | None = None,
    calendar_id: str = "primary",
    decomposition_service: DecompositionService | None = None,
) -> None:
    """Register all slash commands on the bot's command tree.

    Args:
        bot: The Donna Discord client whose command tree receives the commands.
        db: Task database for reads/writes.
        user_id: The owning user the commands operate on behalf of.
        calendar_client: Optional calendar client (enables event deletion on
            cancel). ``None`` disables calendar side effects.
        calendar_id: Calendar id used for those side effects.
        decomposition_service: Optional direct task-decomposition service
            (§7.2 resolution R2). When provided, the ``/breakdown`` command is
            registered; when ``None`` it is silently omitted, so boots without a
            router-backed service simply don't expose it.
    """

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
            await db.transition_task_state(task_id, TaskStatus.CANCELLED)
            if task.calendar_event_id and calendar_client is not None:
                try:
                    await calendar_client.delete_event(
                        calendar_id, task.calendar_event_id,
                    )
                except Exception:
                    logger.warning(
                        "cancel_calendar_delete_failed",
                        task_id=task_id,
                        event_id=task.calendar_event_id,
                    )
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
        embed.add_field(
            name="Scheduled",
            value=t.scheduled_start[:16] if t.scheduled_start else "N/A",
        )
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
    # /breakdown  (§7.2 resolution R2 — decomposition as a direct service)
    # ------------------------------------------------------------------
    if decomposition_service is not None:

        @bot.tree.command(
            name="breakdown",
            description="Break a complex task into sequenced subtasks",
            guild=guild,
        )
        @app_commands.describe(task_id="The task to break down")
        async def breakdown_cmd(interaction: Interaction, task_id: str) -> None:
            await _handle_breakdown(
                interaction=interaction,
                task_id=task_id,
                db=db,
                service=decomposition_service,
            )

        @breakdown_cmd.autocomplete("task_id")
        async def breakdown_autocomplete(
            interaction: Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            return await _task_autocomplete(interaction, current, db)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _handle_breakdown(
    *,
    interaction: Interaction,
    task_id: str,
    db: Database,
    service: DecompositionService,
) -> None:
    """Run task decomposition for ``/breakdown`` and report the plan.

    Extracted from the registration closure so unit tests can drive it
    without a live Discord client. ``DecompositionService.decompose`` makes
    an LLM request — well over Discord's 3-second ack window — so the
    interaction is *deferred* first and the result delivered as a followup.

    The decompose call persists each subtask as a real Task row (parent_task
    set, dependency indices resolved to UUIDs); this handler only triggers it
    and renders what was created. It calls the service directly rather than
    through any dispatcher (design §7.2 resolution; CLAUDE.md principle #4).

    Args:
        interaction: The Discord interaction to acknowledge and reply to.
        task_id: Id of the parent task to break down.
        db: Task database (used to re-read the created subtasks for display).
        service: The direct decomposition service.

    Returns:
        None. Replies are sent on the interaction.
    """
    task = await db.get_task(task_id)
    if task is None:
        await interaction.response.send_message("Task not found.", ephemeral=True)
        return

    # An LLM call is ahead — acknowledge now, answer in a followup.
    await interaction.response.defer(ephemeral=True)

    try:
        result = await service.decompose(task_id)
    except Exception:
        logger.exception("slash_breakdown_failed", task_id=task_id)
        await interaction.followup.send(
            "Couldn't break that task down — try again shortly.",
            ephemeral=True,
        )
        return

    if not result.subtask_ids:
        await interaction.followup.send(
            f"'{task.title}' looks atomic — nothing to break down.",
            ephemeral=True,
        )
        logger.info("slash_breakdown_empty", task_id=task_id)
        return

    subtasks = [await db.get_task(sid) for sid in result.subtask_ids]
    embed = _build_breakdown_embed(parent=task, subtasks=subtasks, result=result)
    await interaction.followup.send(embed=embed, ephemeral=True)
    logger.info(
        "slash_breakdown",
        task_id=task_id,
        subtask_count=len(result.subtask_ids),
        total_hours=result.total_estimated_hours,
    )


def _dependency_positions(
    subtask: TaskRow, pos_by_id: dict[str, int]
) -> list[int]:
    """Resolve a subtask's dependency UUIDs to 1-based plan positions."""
    if not subtask.dependencies:
        return []
    try:
        dep_ids = json.loads(subtask.dependencies)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(dep_ids, list):
        return []
    return sorted(pos_by_id[d] for d in dep_ids if d in pos_by_id)


def _build_breakdown_embed(
    *,
    parent: TaskRow,
    subtasks: list[TaskRow | None],
    result: DecomposeResult,
) -> discord.Embed:
    """Render the created subtask plan as a Discord embed."""
    pos_by_id = {st.id: i + 1 for i, st in enumerate(subtasks) if st is not None}

    lines: list[str] = []
    for i, st in enumerate(subtasks, start=1):
        if st is None:
            continue
        dur = f" · {st.estimated_duration}min" if st.estimated_duration else ""
        deps = _dependency_positions(st, pos_by_id)
        after = (
            f"  ⟵ after {', '.join(f'#{d}' for d in deps)}" if deps else ""
        )
        lines.append(f"**{i}.** {st.title}{dur}{after}")

    embed = discord.Embed(
        title=f"Broke down: {parent.title[:200]}",
        description="\n".join(lines),
        colour=EMBED_COLOUR,
    )
    embed.set_footer(
        text=(
            f"{len(pos_by_id)} subtask(s) created · "
            f"~{result.total_estimated_hours:g}h total"
        )
    )
    if result.deadline_feasible is False:
        embed.add_field(
            name="⏰ Deadline concern",
            value="The estimated work may not fit before the deadline.",
            inline=False,
        )
    if result.missing_information:
        questions = "\n".join(
            f"• {item.get('question', '')}"
            for item in result.missing_information[:5]
            if item.get("question")
        )
        if questions:
            embed.add_field(
                name="⚠️ Open questions", value=questions[:1024], inline=False
            )
    return embed


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
        time_str = (
            t.scheduled_start[11:16]
            if t.scheduled_start and len(t.scheduled_start) > 16
            else "??:??"
        )
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
