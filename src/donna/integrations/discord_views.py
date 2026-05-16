"""Discord UI components — buttons, dropdowns, modals for task interaction.

Provides interactive Views attached to task confirmations, overdue nudges,
digest items, and agent approval flows. All Views encode task_id in custom_id
for persistence across bot restarts.

See docs/notifications.md and the discord interaction expansion plan.
"""

from __future__ import annotations

import contextlib
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

    title_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label="Title",
        placeholder="Task title",
        max_length=500,
    )
    description_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label="Description",
        style=TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    notes_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label="Notes",
        style=TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    priority_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
        label="Priority (1-5)",
        placeholder="1=low, 5=critical",
        max_length=1,
        required=False,
    )
    domain_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
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
                await self._db.update_task(self._task_id, source="discord_modal", **updates)
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

    def __init__(
        self,
        task_id: str,
        db: Database,
        timeout: float = 3600,
        calendar_client: Any | None = None,
        calendar_id: str = "primary",
    ) -> None:
        super().__init__(timeout=timeout)
        self._task_id = task_id
        self._db = db
        self._calendar_client = calendar_client
        self._calendar_id = calendar_id

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
            task = await self._db.get_task(self._task_id)
            await self._db.transition_task_state(self._task_id, TaskStatus.CANCELLED)
            if task and task.calendar_event_id and self._calendar_client is not None:
                try:
                    await self._calendar_client.delete_event(
                        self._calendar_id, task.calendar_event_id,
                    )
                except Exception:
                    logger.warning(
                        "cancel_calendar_delete_failed",
                        task_id=self._task_id,
                        event_id=task.calendar_event_id,
                    )
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
            await self._db.update_task(self._task_id, source="discord_select", priority=value)
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
            await self._db.update_task(self._task_id, source="discord_select", domain=TaskDomain(value))
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


# ------------------------------------------------------------------
# Slice 17 — over-budget escalation view
# ------------------------------------------------------------------


class BudgetEscalationView(discord.ui.View):
    """Four-button view for the over-budget decision tree.

    Buttons render conditionally based on ``offered_modes``. Slice 17
    ships Pause + Cancel; slice 18 adds ``api_extended`` with the real
    dollar amount on the button label. Manual modes ship in slices 20/21.

    See docs/superpowers/specs/manual-escalation.md §4 / §10.1.
    """

    def __init__(
        self,
        *,
        correlation_id: str,
        offered_modes: list[str],
        owner_discord_id: int,
        gate: Any,
        task_id: str | None = None,
        estimate_usd: float | None = None,
        timeout_seconds: float = 3600,
    ) -> None:
        super().__init__(timeout=timeout_seconds)
        self._correlation_id = correlation_id
        self._offered_modes = offered_modes
        self._owner_discord_id = owner_discord_id
        self._gate = gate
        self._task_id = task_id

        # Buttons in spec order: [Approve $X extension] [Manual] [Pause] [Cancel]
        if "api_extended" in offered_modes:
            amount_label = (
                f"Approve ${estimate_usd:.2f} extension"
                if estimate_usd is not None
                else "Approve extension"
            )
            self.add_item(
                _ModeButton(
                    label=amount_label,
                    style=ButtonStyle.green,
                    mode="api_extended",
                )
            )
        # Slice 21: render mode-specific manual buttons.
        # ``claude_code`` rendering routes through the gate's
        # ``record_manual_handoff()`` so the spec file is written +
        # the row is resolved as a single transaction. ``chat`` (slice
        # 20) reuses the generic ``record_user_resolution`` path. The
        # gate's per-task-type config typically narrows offered_modes
        # to at most one of these per row, but we render whichever
        # tokens are present so a future config could surface both.
        if "claude_code" in offered_modes:
            self.add_item(
                _ClaudeCodeHandoffButton(label="Claude Code")
            )
        if "chat" in offered_modes:
            self.add_item(
                _ModeButton(
                    label="Chat",
                    style=ButtonStyle.blurple,
                    mode="chat",
                )
            )
        # Backwards-compatible "Manual handoff" path: only render when
        # the gate sent the legacy ``manual`` token alone (no
        # mode-specific token). Existing slice 17/18 deployments fall
        # here. Slice 20's :meth:`_pick_manual_mode` reduces multi-mode
        # views to a single label for that legacy path.
        legacy_manual_mode = self._pick_manual_mode(offered_modes)
        if (
            legacy_manual_mode == "manual"
            and "claude_code" not in offered_modes
            and "chat" not in offered_modes
        ):
            self.add_item(
                _ModeButton(
                    label="Manual handoff",
                    style=ButtonStyle.blurple,
                    mode=legacy_manual_mode,
                )
            )
        if "pause" in offered_modes:
            self.add_item(
                _ModeButton(
                    label="Pause",
                    style=ButtonStyle.gray,
                    mode="pause",
                )
            )
        if "cancel" in offered_modes:
            self.add_item(
                _ModeButton(
                    label="Cancel",
                    style=ButtonStyle.red,
                    mode="cancel",
                )
            )

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    @property
    def owner_discord_id(self) -> int:
        return self._owner_discord_id

    @property
    def gate(self) -> Any:
        return self._gate

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @staticmethod
    def _pick_manual_mode(offered_modes: list[str]) -> str | None:
        """Resolve which specific manual mode the handoff button should
        carry. ``chat`` (slice 20) is preferred over ``claude_code``
        (slice 21) when both happen to be present, but in practice the
        gate's per-task-type routing only ever puts one in
        ``offered_modes``. Falls back to the legacy ``"manual"`` literal
        only when neither is present (older callers / fixtures).
        """
        if "chat" in offered_modes:
            return "chat"
        if "claude_code" in offered_modes:
            return "claude_code"
        if "manual" in offered_modes:
            return "manual"
        return None


class _ModeButton(discord.ui.Button[discord.ui.View]):
    """Single button on a :class:`BudgetEscalationView`.

    Owner-ID check, stale-click guard, and audit write all happen
    here. The view's :meth:`stop` is called on success so further
    clicks are no-ops.
    """

    def __init__(
        self,
        *,
        label: str,
        style: ButtonStyle,
        mode: str,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=f"escalation_{mode}")
        self._mode = mode

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        if not isinstance(view, BudgetEscalationView):
            return

        # Owner check — only the configured owner Discord ID can resolve.
        if interaction.user.id != view.owner_discord_id:
            logger.warning(
                "escalation_owner_mismatch",
                correlation_id=view.correlation_id,
                actual_user_id=interaction.user.id,
                expected_user_id=view.owner_discord_id,
                mode=self._mode,
            )
            await interaction.response.send_message(
                "Only the account owner can resolve this.", ephemeral=True
            )
            return

        # For api_extended: persist the extension row BEFORE resolving the
        # escalation. This ordering ensures the extension exists if the
        # orchestrator crashes between grant and resolution. The operation is
        # idempotent — a Discord retry will find the existing row.
        if self._mode == "api_extended":
            try:
                await view.gate.grant_budget_extension(
                    correlation_id=view.correlation_id,
                    granted_by=str(interaction.user.id),
                )
            except Exception:
                logger.exception(
                    "budget_extension_grant_failed",
                    correlation_id=view.correlation_id,
                    mode=self._mode,
                )
                await interaction.response.send_message(
                    "Couldn't grant extension — please try again or check the dashboard.",
                    ephemeral=True,
                )
                return

        # The gate's repository handles atomicity — `record_user_resolution`
        # returns False if the row was already resolved (timeout sweep won
        # the race, or another click already resolved it).
        try:
            mutated = await view.gate.record_user_resolution(
                correlation_id=view.correlation_id,
                mode=self._mode,
                owner_user_id=str(interaction.user.id),
                task_id=view.task_id,
            )
        except Exception:
            logger.exception(
                "escalation_resolve_failed",
                correlation_id=view.correlation_id,
                mode=self._mode,
            )
            await interaction.response.send_message(
                "Couldn't resolve this — try again or check the dashboard.",
                ephemeral=True,
            )
            return

        if not mutated:
            await interaction.response.send_message(
                "This escalation was already resolved.", ephemeral=True
            )
            view.stop()
            return

        await interaction.response.send_message(
            f"Resolved: {self._mode}.", ephemeral=True
        )
        logger.info(
            "escalation_resolved_via_button",
            correlation_id=view.correlation_id,
            mode=self._mode,
            user_id=interaction.user.id,
        )
        view.stop()


class _ClaudeCodeHandoffButton(discord.ui.Button[discord.ui.View]):
    """Slice 21 — render the spec file + resolve as ``claude_code``.

    Distinct from :class:`_ModeButton` because the manual handoff path
    requires an extra atomic step (rendering the spec to disk and
    mirroring into ``escalation_request.prompt_body``) BEFORE the
    resolution event fires. Implemented as a separate button class so
    the resolution-specific code path stays linear and the audit log
    captures ``mode='claude_code'`` with a stable spec_path payload.
    """

    def __init__(self, *, label: str = "Claude Code") -> None:
        super().__init__(
            label=label,
            style=ButtonStyle.blurple,
            custom_id="escalation_claude_code",
        )

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        if not isinstance(view, BudgetEscalationView):
            return

        if interaction.user.id != view.owner_discord_id:
            logger.warning(
                "escalation_owner_mismatch",
                correlation_id=view.correlation_id,
                actual_user_id=interaction.user.id,
                expected_user_id=view.owner_discord_id,
                mode="claude_code",
            )
            await interaction.response.send_message(
                "Only the account owner can resolve this.", ephemeral=True
            )
            return

        try:
            rendered = await view.gate.record_manual_handoff(
                correlation_id=view.correlation_id,
                mode="claude_code",
                actor_id=str(interaction.user.id),
            )
        except Exception:
            logger.exception(
                "claude_code_handoff_failed",
                correlation_id=view.correlation_id,
            )
            await interaction.response.send_message(
                "Couldn't prepare the Claude Code handoff — check the dashboard.",
                ephemeral=True,
            )
            return

        if rendered is None:
            await interaction.response.send_message(
                "This escalation can't be handed off to Claude Code "
                "(missing config or already resolved).",
                ephemeral=True,
            )
            view.stop()
            return

        await interaction.response.send_message(
            (
                f"Spec written to `{rendered.path}`.\n"
                f"Branch: `{rendered.branch_name}`.\n"
                f"Open the dashboard for the worktree command and "
                f"target paths."
            ),
            ephemeral=True,
        )
        logger.info(
            "claude_code_handoff_recorded",
            correlation_id=view.correlation_id,
            spec_path=str(rendered.path),
            branch_name=rendered.branch_name,
            user_id=interaction.user.id,
        )
        view.stop()


# ---------------------------------------------------------------------------
# Slice 22 — tool gap ping view
# ---------------------------------------------------------------------------


class ToolGapPingView(discord.ui.View):
    """High-blocking tool-gap ping with ``[File request] [Snooze 24h]``.

    Posted by :class:`donna.cost.tool_gap_surfacer.ToolGapSurfacer`
    when a high-severity gap is detected. Owner-ID check, stale-click
    guard, and audit writes mirror :class:`BudgetEscalationView`.

    See docs/superpowers/specs/manual-escalation.md §7.
    """

    def __init__(
        self,
        *,
        tool_request_id: int,
        tool_name: str,
        owner_discord_id: int,
        gate: Any,
        tool_request_repo: Any,
        snooze_seconds: int = 86400,
        timeout_seconds: float = 3600,
    ) -> None:
        super().__init__(timeout=timeout_seconds)
        self._tool_request_id = tool_request_id
        self._tool_name = tool_name
        self._owner_discord_id = owner_discord_id
        self._gate = gate
        self._tool_request_repo = tool_request_repo
        self._snooze_seconds = snooze_seconds
        self.add_item(
            _ToolGapFileRequestButton(tool_request_id=tool_request_id)
        )
        self.add_item(
            _ToolGapSnoozeButton(
                tool_request_id=tool_request_id,
                snooze_seconds=snooze_seconds,
            )
        )

    @property
    def tool_request_id(self) -> int:
        return self._tool_request_id

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def owner_discord_id(self) -> int:
        return self._owner_discord_id

    @property
    def gate(self) -> Any:
        return self._gate

    @property
    def tool_request_repo(self) -> Any:
        return self._tool_request_repo

    @property
    def snooze_seconds(self) -> int:
        return self._snooze_seconds


def _disable_all(view: discord.ui.View) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = True


class _ToolGapFileRequestButton(discord.ui.Button[discord.ui.View]):
    """[File request] — open a tool_request_fulfillment escalation."""

    def __init__(self, *, tool_request_id: int) -> None:
        super().__init__(
            label="File request",
            style=ButtonStyle.green,
            custom_id=f"tool_gap_file_{tool_request_id}",
        )

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        if not isinstance(view, ToolGapPingView):
            return
        if interaction.user.id != view.owner_discord_id:
            logger.warning(
                "tool_gap_owner_mismatch",
                tool_request_id=view.tool_request_id,
                actual_user_id=interaction.user.id,
                expected_user_id=view.owner_discord_id,
                button="file_request",
            )
            await interaction.response.send_message(
                "Only the account owner can resolve this.", ephemeral=True
            )
            return

        # Stale-click guard.
        row = await view.tool_request_repo.get(view.tool_request_id)
        if row is None:
            await interaction.response.send_message(
                "Tool request not found.", ephemeral=True
            )
            view.stop()
            return
        if row.status != "open":
            await interaction.response.send_message(
                f"Tool request already {row.status}.", ephemeral=True
            )
            _disable_all(view)
            with contextlib.suppress(Exception):
                await interaction.message.edit(view=view)  # type: ignore[union-attr]
            view.stop()
            return

        try:
            esc_row, _rendered = await view.gate.open_tool_build_escalation(
                tool_request_id=view.tool_request_id,
                tool_name=view.tool_name,
                user_id=row.user_id,
                priority=row.priority,
                actor_id=str(interaction.user.id),
                proposed_signature=row.proposed_signature,
            )
        except Exception:
            logger.exception(
                "tool_gap_file_request_failed",
                tool_request_id=view.tool_request_id,
            )
            await interaction.response.send_message(
                "Couldn't file the request — check the dashboard.",
                ephemeral=True,
            )
            return

        await view.tool_request_repo.mark_in_progress(
            view.tool_request_id,
            escalation_request_id=esc_row.id,
        )
        _disable_all(view)
        with contextlib.suppress(Exception):
            await interaction.message.edit(view=view)  # type: ignore[union-attr]

        await interaction.response.send_message(
            (
                f"Filed tool build request for `{view.tool_name}` — "
                f"escalation `{esc_row.correlation_id}`. "
                "Open the dashboard for the worktree command."
            ),
            ephemeral=True,
        )
        logger.info(
            "tool_gap_filed_via_button",
            tool_request_id=view.tool_request_id,
            tool_name=view.tool_name,
            escalation_request_id=esc_row.id,
            user_id=interaction.user.id,
        )
        view.stop()


class _ToolGapSnoozeButton(discord.ui.Button[discord.ui.View]):
    """[Snooze 24h] — set ``snoozed_until`` on the row."""

    def __init__(self, *, tool_request_id: int, snooze_seconds: int) -> None:
        super().__init__(
            label="Snooze 24h",
            style=ButtonStyle.gray,
            custom_id=f"tool_gap_snooze_{tool_request_id}",
        )
        self._snooze_seconds = snooze_seconds

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        if not isinstance(view, ToolGapPingView):
            return
        if interaction.user.id != view.owner_discord_id:
            logger.warning(
                "tool_gap_owner_mismatch",
                tool_request_id=view.tool_request_id,
                actual_user_id=interaction.user.id,
                expected_user_id=view.owner_discord_id,
                button="snooze",
            )
            await interaction.response.send_message(
                "Only the account owner can resolve this.", ephemeral=True
            )
            return
        try:
            ok = await view.tool_request_repo.snooze(
                view.tool_request_id, seconds=self._snooze_seconds
            )
        except Exception:
            logger.exception(
                "tool_gap_snooze_failed",
                tool_request_id=view.tool_request_id,
            )
            await interaction.response.send_message(
                "Couldn't snooze — try again.", ephemeral=True
            )
            return
        if not ok:
            await interaction.response.send_message(
                "Tool request is no longer open.", ephemeral=True
            )
            _disable_all(view)
            with contextlib.suppress(Exception):
                await interaction.message.edit(view=view)  # type: ignore[union-attr]
            view.stop()
            return
        _disable_all(view)
        with contextlib.suppress(Exception):
            await interaction.message.edit(view=view)  # type: ignore[union-attr]
        hours = self._snooze_seconds // 3600
        await interaction.response.send_message(
            f"Snoozed `{view.tool_name}` for {hours}h.", ephemeral=True
        )
        logger.info(
            "tool_gap_snoozed_via_button",
            tool_request_id=view.tool_request_id,
            tool_name=view.tool_name,
            user_id=interaction.user.id,
            snooze_seconds=self._snooze_seconds,
        )
        view.stop()
