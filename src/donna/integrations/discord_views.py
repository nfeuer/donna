"""Discord UI components — buttons, dropdowns, modals for task interaction.

Provides interactive Views attached to task confirmations, overdue nudges,
digest items, and agent approval flows. All Views encode task_id in custom_id
for persistence across bot restarts.

See docs/notifications.md and the discord interaction expansion plan.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

import discord
import structlog
from discord import ButtonStyle, Interaction, SelectOption, TextStyle

if TYPE_CHECKING:
    from donna.tasks.database import Database

logger = structlog.get_logger()


# ------------------------------------------------------------------
# Task Edit Modal
# ------------------------------------------------------------------


class TaskEditModal(discord.ui.Modal, title="Edit Task"):
    """Modal for editing task fields inline.

    Pre-fills current values. On submit, updates the task in DB and
    sends a confirmation with changed fields highlighted.
    """

    title_input = discord.ui.TextInput(
        label="Title",
        placeholder="Task title",
        max_length=500,
    )
    description_input = discord.ui.TextInput(
        label="Description",
        style=TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    notes_input = discord.ui.TextInput(
        label="Notes",
        style=TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    priority_input = discord.ui.TextInput(
        label="Priority (1-5)",
        placeholder="1=low, 5=critical",
        max_length=1,
        required=False,
    )
    domain_input = discord.ui.TextInput(
        label="Domain (personal/work/family)",
        placeholder="personal, work, or family",
        max_length=10,
        required=False,
    )

    def __init__(
        self,
        task_id: str,
        db: Database,
        current_title: str = "",
        current_description: str = "",
        current_notes: str = "",
        current_priority: str = "",
        current_domain: str = "",
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._db = db
        self._original = {
            "title": current_title,
            "description": current_description,
            "notes": current_notes,
            "priority": current_priority,
            "domain": current_domain,
        }
        self.title_input.default = current_title
        self.description_input.default = current_description
        self.notes_input.default = current_notes
        self.priority_input.default = current_priority
        self.domain_input.default = current_domain

    async def on_submit(self, interaction: Interaction) -> None:
        """Apply changes and report what was modified."""
        from donna.tasks.db_models import TaskDomain

        updates: dict[str, Any] = {}
        changes: list[str] = []

        new_title = self.title_input.value.strip()
        if new_title and new_title != self._original["title"]:
            updates["title"] = new_title
            changes.append(f"**Title**: {new_title}")

        new_desc = self.description_input.value.strip()
        if new_desc != (self._original["description"] or ""):
            updates["description"] = new_desc or None
            changes.append("**Description** updated")

        new_notes = self.notes_input.value.strip()
        if new_notes != (self._original["notes"] or ""):
            import json
            updates["notes"] = json.dumps([new_notes]) if new_notes else None
            changes.append("**Notes** updated")

        new_priority = self.priority_input.value.strip()
        if new_priority and new_priority != self._original["priority"]:
            try:
                p = int(new_priority)
                if 1 <= p <= 5:
                    updates["priority"] = p
                    changes.append(f"**Priority**: {p}")
            except ValueError:
                pass

        new_domain = self.domain_input.value.strip().lower()
        if new_domain and new_domain != self._original["domain"]:
            try:
                domain_enum = TaskDomain(new_domain)
                updates["domain"] = domain_enum
                changes.append(f"**Domain**: {new_domain}")
            except ValueError:
                pass

        if updates:
            try:
                await self._db.update_task(self._task_id, **updates)
                summary = "\n".join(changes)
                await interaction.response.send_message(
                    f"Task updated:\n{summary}", ephemeral=True
                )
                logger.info(
                    "task_edited_via_modal",
                    task_id=self._task_id,
                    fields=list(updates.keys()),
                )
            except Exception:
                logger.exception("task_edit_modal_failed", task_id=self._task_id)
                await interaction.response.send_message(
                    "Failed to update task. Please try again.", ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "No changes detected.", ephemeral=True
            )


# ------------------------------------------------------------------
# Task Confirmation View (after task creation)
# ------------------------------------------------------------------


class TaskConfirmationView(discord.ui.View):
    """Buttons on task creation confirmation: Mark Done, Edit, Reschedule."""

    def __init__(self, task_id: str, db: Database, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self._task_id = task_id
        self._db = db

    @discord.ui.button(label="Mark Done", style=ButtonStyle.green, custom_id="confirm_done")
    async def mark_done(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        from donna.tasks.db_models import TaskStatus

        try:
            await self._db.update_task(self._task_id, status=TaskStatus.DONE)
            await interaction.response.send_message(
                "Task marked as done.", ephemeral=True
            )
            logger.info("task_done_via_button", task_id=self._task_id)
        except Exception:
            logger.exception("task_done_button_failed", task_id=self._task_id)
            await interaction.response.send_message(
                "Failed to update task.", ephemeral=True
            )
        self.stop()

    @discord.ui.button(label="Edit", style=ButtonStyle.grey, custom_id="confirm_edit")
    async def edit_task(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        task = await self._db.get_task(self._task_id)
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
            task_id=self._task_id,
            db=self._db,
            current_title=task.title,
            current_description=task.description or "",
            current_notes=notes_str,
            current_priority=str(task.priority),
            current_domain=task.domain,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Reschedule", style=ButtonStyle.blurple, custom_id="confirm_reschedule",
    )
    async def reschedule(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        await interaction.response.send_message(
            "Use `/reschedule` to pick a new time, or reply with a time like 'tomorrow 2pm'.",
            ephemeral=True,
        )


# ------------------------------------------------------------------
# Overdue Nudge View
# ------------------------------------------------------------------


class OverdueNudgeView(discord.ui.View):
    """Buttons on overdue nudges: Done, +30min, Reschedule, Cancel."""

    def __init__(self, task_id: str, db: Database, timeout: float = 3600) -> None:
        super().__init__(timeout=timeout)
        self._task_id = task_id
        self._db = db

    @discord.ui.button(label="Done", style=ButtonStyle.green, custom_id="overdue_done")
    async def done(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        from donna.tasks.db_models import TaskStatus

        try:
            await self._db.update_task(self._task_id, status=TaskStatus.DONE)
            await interaction.response.send_message("Marked as done.", ephemeral=True)
            logger.info("overdue_done_via_button", task_id=self._task_id)
        except Exception:
            logger.exception("overdue_done_failed", task_id=self._task_id)
            await interaction.response.send_message("Failed to update.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="+30min", style=ButtonStyle.blurple, custom_id="overdue_snooze")
    async def snooze(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        from datetime import datetime, timedelta

        try:
            task = await self._db.get_task(self._task_id)
            if task and task.scheduled_start:
                current = datetime.fromisoformat(task.scheduled_start)
            else:
                current = datetime.now(tz=UTC)
            new_start = current + timedelta(minutes=30)
            await self._db.update_task(
                self._task_id, scheduled_start=new_start.isoformat()
            )
            await interaction.response.send_message(
                f"Snoozed +30min to {new_start.strftime('%H:%M')}.", ephemeral=True
            )
            logger.info("overdue_snoozed", task_id=self._task_id, new_start=new_start.isoformat())
        except Exception:
            logger.exception("overdue_snooze_failed", task_id=self._task_id)
            await interaction.response.send_message("Failed to snooze.", ephemeral=True)

    @discord.ui.button(label="Reschedule", style=ButtonStyle.grey, custom_id="overdue_reschedule")
    async def reschedule(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        await interaction.response.send_message(
            "Use `/reschedule` to pick a new time.", ephemeral=True
        )

    @discord.ui.button(label="Cancel", style=ButtonStyle.red, custom_id="overdue_cancel")
    async def cancel(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        from donna.tasks.db_models import TaskStatus

        try:
            await self._db.update_task(self._task_id, status=TaskStatus.CANCELLED)
            await interaction.response.send_message("Task cancelled.", ephemeral=True)
            logger.info("overdue_cancelled_via_button", task_id=self._task_id)
        except Exception:
            logger.exception("overdue_cancel_failed", task_id=self._task_id)
            await interaction.response.send_message("Failed to cancel.", ephemeral=True)
        self.stop()


# ------------------------------------------------------------------
# Priority Select View
# ------------------------------------------------------------------


class PrioritySelectView(discord.ui.View):
    """Dropdown for selecting priority 1-5."""

    def __init__(self, task_id: str, db: Database, timeout: float = 120) -> None:
        super().__init__(timeout=timeout)
        self._task_id = task_id
        self._db = db

    @discord.ui.select(
        placeholder="Choose priority...",
        options=[
            SelectOption(label="1 - Low", value="1"),
            SelectOption(label="2 - Normal", value="2"),
            SelectOption(label="3 - Medium", value="3"),
            SelectOption(label="4 - High", value="4"),
            SelectOption(label="5 - Critical", value="5"),
        ],
    )
    async def priority_callback(
        self, interaction: Interaction, select: discord.ui.Select  # type: ignore[type-arg]
    ) -> None:
        value = int(select.values[0])
        try:
            await self._db.update_task(self._task_id, priority=value)
            await interaction.response.send_message(
                f"Priority set to {value}.", ephemeral=True
            )
            logger.info("priority_updated_via_select", task_id=self._task_id, priority=value)
        except Exception:
            logger.exception("priority_select_failed", task_id=self._task_id)
            await interaction.response.send_message("Failed to update.", ephemeral=True)
        self.stop()


# ------------------------------------------------------------------
# Domain Select View
# ------------------------------------------------------------------


class DomainSelectView(discord.ui.View):
    """Dropdown for selecting domain."""

    def __init__(self, task_id: str, db: Database, timeout: float = 120) -> None:
        super().__init__(timeout=timeout)
        self._task_id = task_id
        self._db = db

    @discord.ui.select(
        placeholder="Choose domain...",
        options=[
            SelectOption(label="Personal", value="personal"),
            SelectOption(label="Work", value="work"),
            SelectOption(label="Family", value="family"),
        ],
    )
    async def domain_callback(
        self, interaction: Interaction, select: discord.ui.Select  # type: ignore[type-arg]
    ) -> None:
        from donna.tasks.db_models import TaskDomain

        value = select.values[0]
        try:
            await self._db.update_task(self._task_id, domain=TaskDomain(value))
            await interaction.response.send_message(
                f"Domain set to {value}.", ephemeral=True
            )
            logger.info("domain_updated_via_select", task_id=self._task_id, domain=value)
        except Exception:
            logger.exception("domain_select_failed", task_id=self._task_id)
            await interaction.response.send_message("Failed to update.", ephemeral=True)
        self.stop()


# ------------------------------------------------------------------
# Task List Pagination View
# ------------------------------------------------------------------


class TaskListPaginationView(discord.ui.View):
    """Previous/Next buttons for paginated task lists with per-task actions."""

    def __init__(
        self,
        tasks: list[Any],
        page: int,
        per_page: int = 10,
        db: Database | None = None,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self._tasks = tasks
        self._page = page
        self._per_page = per_page
        self._db = db
        self._total_pages = max(1, (len(tasks) + per_page - 1) // per_page)

        # Disable buttons at boundaries.
        if page <= 0:
            self.previous_page.disabled = True
        if page >= self._total_pages - 1:
            self.next_page.disabled = True

    def get_page_tasks(self) -> list[Any]:
        """Return tasks for the current page."""
        start = self._page * self._per_page
        return self._tasks[start : start + self._per_page]

    def build_embed(self, title: str = "Tasks") -> discord.Embed:
        """Build an embed for the current page."""
        page_tasks = self.get_page_tasks()
        description_lines = []
        for t in page_tasks:
            status_icon = {
                "backlog": "📋", "scheduled": "📅", "in_progress": "🔄",
                "blocked": "🚫", "waiting_input": "❓", "done": "✅", "cancelled": "❌",
            }.get(t.status, "📋")
            short_id = t.id[:8]
            line = f"{status_icon} **{t.title}** `{short_id}` — P{t.priority} {t.domain}"
            if t.scheduled_start:
                line += f" | {t.scheduled_start[:16]}"
            description_lines.append(line)

        embed = discord.Embed(
            title=title,
            description="\n".join(description_lines) if description_lines else "No tasks found.",
            colour=0x5865F2,
        )
        embed.set_footer(text=f"Page {self._page + 1}/{self._total_pages}")
        return embed

    @discord.ui.button(label="Previous", style=ButtonStyle.grey, custom_id="page_prev")
    async def previous_page(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        self._page = max(0, self._page - 1)
        self._update_button_states()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=ButtonStyle.grey, custom_id="page_next")
    async def next_page(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        self._page = min(self._total_pages - 1, self._page + 1)
        self._update_button_states()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def _update_button_states(self) -> None:
        self.previous_page.disabled = self._page <= 0
        self.next_page.disabled = self._page >= self._total_pages - 1


# ------------------------------------------------------------------
# Agent Approval View
# ------------------------------------------------------------------


class AgentApprovalView(discord.ui.View):
    """Approve/Reject buttons for agent actions requiring user consent."""

    def __init__(
        self,
        task_id: str,
        agent_name: str,
        action_description: str,
        on_approve: Any = None,
        on_reject: Any = None,
        timeout: float = 3600,
    ) -> None:
        super().__init__(timeout=timeout)
        self._task_id = task_id
        self._agent_name = agent_name
        self._action_description = action_description
        self._on_approve = on_approve
        self._on_reject = on_reject

    @discord.ui.button(label="Approve", style=ButtonStyle.green, custom_id="agent_approve")
    async def approve(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if self._on_approve:
            try:
                await self._on_approve(self._task_id)
                await interaction.response.send_message(
                    f"Approved: {self._action_description}", ephemeral=True
                )
                logger.info(
                    "agent_action_approved",
                    task_id=self._task_id,
                    agent=self._agent_name,
                )
            except Exception:
                logger.exception("agent_approve_failed", task_id=self._task_id)
                await interaction.response.send_message("Approval failed.", ephemeral=True)
        else:
            await interaction.response.send_message("Approved.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Reject", style=ButtonStyle.red, custom_id="agent_reject")
    async def reject(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        if self._on_reject:
            try:
                await self._on_reject(self._task_id)
            except Exception:
                logger.exception("agent_reject_failed", task_id=self._task_id)
        await interaction.response.send_message(
            f"Rejected: {self._action_description}", ephemeral=True
        )
        logger.info(
            "agent_action_rejected",
            task_id=self._task_id,
            agent=self._agent_name,
        )
        self.stop()


# ------------------------------------------------------------------
# Persistent View Dispatcher
# ------------------------------------------------------------------


class PersistentTaskView(discord.ui.View):
    """Persistent view that survives bot restarts.

    Parses action and task_id from custom_id (format: "action:task_id").
    Register once in on_ready via bot.add_view(PersistentTaskView(db)).
    """

    def __init__(self, db: Database) -> None:
        super().__init__(timeout=None)
        self._db = db


# ------------------------------------------------------------------
# Escalation Approval View
# ------------------------------------------------------------------


class EscalationApprovalView(discord.ui.View):
    """Approve/Decline buttons for Claude escalation."""

    def __init__(self, session_id: str, chat_engine: Any, user_id: str) -> None:
        super().__init__(timeout=300)
        self._session_id = session_id
        self._chat_engine = chat_engine
        self._user_id = user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        await interaction.response.defer()
        resp = await self._chat_engine.handle_escalation(
            session_id=self._session_id, user_id=self._user_id
        )
        await interaction.followup.send(resp.text)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.grey)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        await interaction.response.send_message("Got it, I'll do my best without Claude.")
        self.stop()


# ------------------------------------------------------------------
# Automation Confirmation View (Wave 3 NL-created automations)
# ------------------------------------------------------------------


_CRON_HUMAN_TABLE = {
    "*/15 * * * *": "every 15 minutes",
    "*/30 * * * *": "every 30 minutes",
    "0 * * * *": "hourly",
    "0 */6 * * *": "every 6 hours",
    "0 */12 * * *": "every 12 hours",
    "0 0 * * *": "daily at midnight",
    "0 9 * * *": "daily at 9am",
    "0 12 * * *": "daily at noon",
}


def _cron_to_human(cron: str | None) -> str:
    if cron is None:
        return "paused"
    return _CRON_HUMAN_TABLE.get(cron, cron)


class AutomationConfirmationView(discord.ui.View):
    """Embed card + Approve/Edit/Cancel buttons for a pending DraftAutomation."""

    def __init__(self, *, draft: Any, name: str) -> None:
        super().__init__(timeout=1800)
        self.draft = draft
        self.name = name
        self.result: str | None = None  # approve | edit | cancel

    def build_embed(self) -> discord.Embed:
        cap_desc = (
            f"Capability: `{self.draft.capability_name}`"
            if self.draft.capability_name
            else "Capability: _none — Claude will handle runs_"
        )
        embed = discord.Embed(
            title=f"Create automation: {self.name}",
            description=cap_desc,
            color=discord.Color.blue(),
        )
        inputs_value = (
            "\n".join(f"{k}: `{v}`" for k, v in self.draft.inputs.items())
            or "_(none)_"
        )
        embed.add_field(name="Inputs", value=inputs_value, inline=False)

        if self.draft.target_cadence_cron != self.draft.active_cadence_cron:
            # Mirror the non-clamped branch's fallback: when schedule_human
            # is absent, humanize the target cron instead of echoing the
            # raw expression.
            target_human = self.draft.schedule_human or _cron_to_human(
                self.draft.target_cadence_cron
            )
            active_human = _cron_to_human(self.draft.active_cadence_cron)
            embed.add_field(
                name="Schedule",
                value=(
                    f"Your target: **{target_human}**\n"
                    f"Running: **{active_human}** for now\n"
                    "_I'll speed up automatically: hourly once I'm shadowing, "
                    "your target once trusted._"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Schedule",
                value=(
                    self.draft.schedule_human
                    or self.draft.schedule_cron
                    or "(none)"
                ),
                inline=False,
            )

        if self.draft.alert_conditions:
            embed.add_field(
                name="Alert when",
                value=f"`{self.draft.alert_conditions.get('expression', '')}`",
                inline=False,
            )
        return embed

    @discord.ui.button(label="Approve", style=ButtonStyle.green)
    async def approve(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        self.result = "approve"
        self.stop()
        await interaction.response.edit_message(
            content="Creating automation…", view=None
        )

    @discord.ui.button(label="Edit", style=ButtonStyle.grey)
    async def edit(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        self.result = "edit"
        self.stop()
        await interaction.response.edit_message(
            content="What do you want to change?", view=None
        )

    @discord.ui.button(label="Cancel", style=ButtonStyle.red)
    async def cancel(self, interaction: Interaction, button: discord.ui.Button) -> None:  # type: ignore[type-arg]
        self.result = "cancel"
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled — nothing created.", view=None
        )
