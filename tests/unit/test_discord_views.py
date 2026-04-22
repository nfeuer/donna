"""Unit tests for Discord UI components (Views, Modals, Selects)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from donna.integrations.discord_views import (
    AgentApprovalView,
    DomainSelectView,
    OverdueNudgeView,
    PrioritySelectView,
    TaskConfirmationView,
    TaskEditModal,
    TaskListPaginationView,
)


def _make_task_row(**overrides: object) -> MagicMock:
    """Build a minimal TaskRow-like mock."""
    row = MagicMock()
    row.id = "task-abc-123"
    row.title = "Buy milk"
    row.description = "From the store"
    row.domain = "personal"
    row.priority = 2
    row.status = "backlog"
    row.scheduled_start = "2024-04-05T14:00:00"
    row.estimated_duration = 30
    row.notes = None
    for key, val in overrides.items():
        setattr(row, key, val)
    return row


def _make_interaction() -> MagicMock:
    """Build a mock Discord Interaction."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = "999888777"
    return interaction


class TestTaskConfirmationView:
    @pytest.mark.asyncio
    async def test_mark_done_updates_task(self) -> None:
        db = AsyncMock()
        db.update_task = AsyncMock()
        view = TaskConfirmationView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        # discord.py button callback: bound to the item, takes (interaction)
        await view.mark_done.callback(interaction)

        db.update_task.assert_called_once()
        call_kwargs = db.update_task.call_args
        assert call_kwargs[0][0] == "task-abc-123"
        interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_opens_modal(self) -> None:
        db = AsyncMock()
        task = _make_task_row()
        db.get_task = AsyncMock(return_value=task)
        view = TaskConfirmationView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        await view.edit_task.callback(interaction)

        db.get_task.assert_called_once_with("task-abc-123")
        interaction.response.send_modal.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_task_not_found(self) -> None:
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=None)
        view = TaskConfirmationView(task_id="nonexistent", db=db)
        interaction = _make_interaction()

        await view.edit_task.callback(interaction)

        interaction.response.send_message.assert_called_once()
        sent = interaction.response.send_message.call_args
        assert "not found" in sent[0][0].lower()


class TestOverdueNudgeView:
    @pytest.mark.asyncio
    async def test_done_updates_status(self) -> None:
        db = AsyncMock()
        db.update_task = AsyncMock()
        view = OverdueNudgeView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        await view.done.callback(interaction)

        db.update_task.assert_called_once()
        interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_snooze_extends_schedule(self) -> None:
        db = AsyncMock()
        task = _make_task_row(scheduled_start="2024-04-05T14:00:00")
        db.get_task = AsyncMock(return_value=task)
        db.update_task = AsyncMock()
        view = OverdueNudgeView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        await view.snooze.callback(interaction)

        db.update_task.assert_called_once()
        call_kwargs = db.update_task.call_args
        assert "scheduled_start" in call_kwargs[1]
        assert "14:30" in call_kwargs[1]["scheduled_start"]

    @pytest.mark.asyncio
    async def test_cancel_updates_status(self) -> None:
        db = AsyncMock()
        db.update_task = AsyncMock()
        view = OverdueNudgeView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        await view.cancel.callback(interaction)

        db.update_task.assert_called_once()
        interaction.response.send_message.assert_called_once()


class TestTaskEditModal:
    @pytest.mark.asyncio
    async def test_submit_with_changes(self) -> None:
        db = AsyncMock()
        db.update_task = AsyncMock()
        modal = TaskEditModal(
            task_id="task-abc-123",
            db=db,
            current_title="Buy milk",
            current_priority="2",
            current_domain="personal",
        )
        # Simulate user changing the title.
        modal.title_input._value = "Buy oat milk"
        modal.description_input._value = ""
        modal.notes_input._value = ""
        modal.priority_input._value = "3"
        modal.domain_input._value = "personal"

        interaction = _make_interaction()
        await modal.on_submit(interaction)

        db.update_task.assert_called_once()
        call_kwargs = db.update_task.call_args
        assert call_kwargs[1]["title"] == "Buy oat milk"
        assert call_kwargs[1]["priority"] == 3

    @pytest.mark.asyncio
    async def test_submit_no_changes(self) -> None:
        db = AsyncMock()
        modal = TaskEditModal(
            task_id="task-abc-123",
            db=db,
            current_title="Buy milk",
            current_priority="2",
            current_domain="personal",
        )
        modal.title_input._value = "Buy milk"
        modal.description_input._value = ""
        modal.notes_input._value = ""
        modal.priority_input._value = "2"
        modal.domain_input._value = "personal"

        interaction = _make_interaction()
        await modal.on_submit(interaction)

        db.update_task.assert_not_called()
        sent = interaction.response.send_message.call_args[0][0]
        assert "no changes" in sent.lower()


class TestPrioritySelectView:
    @pytest.mark.asyncio
    async def test_select_updates_priority(self) -> None:
        db = AsyncMock()
        db.update_task = AsyncMock()
        view = PrioritySelectView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        select_item = view.children[0]
        # discord.py Select.values is a read-only property; patch it.
        with patch.object(
            type(select_item), "values",
            new_callable=lambda: property(lambda self: ["4"]),
        ):
            await select_item.callback(interaction)

        db.update_task.assert_called_once_with("task-abc-123", priority=4)


class TestDomainSelectView:
    @pytest.mark.asyncio
    async def test_select_updates_domain(self) -> None:
        db = AsyncMock()
        db.update_task = AsyncMock()
        view = DomainSelectView(task_id="task-abc-123", db=db)
        interaction = _make_interaction()

        select_item = view.children[0]
        with patch.object(
            type(select_item), "values",
            new_callable=lambda: property(lambda self: ["work"]),
        ):
            await select_item.callback(interaction)

        db.update_task.assert_called_once()


class TestTaskListPaginationView:
    def test_build_embed_with_tasks(self) -> None:
        tasks = [_make_task_row(id=f"task-{i}", title=f"Task {i}") for i in range(15)]
        view = TaskListPaginationView(tasks=tasks, page=0)
        embed = view.build_embed(title="Test Tasks")

        assert embed.title == "Test Tasks"
        assert "Page 1/2" in embed.footer.text

    def test_build_embed_empty(self) -> None:
        view = TaskListPaginationView(tasks=[], page=0)
        embed = view.build_embed()

        assert "No tasks found" in embed.description

    def test_button_states_first_page(self) -> None:
        tasks = [_make_task_row(id=f"task-{i}") for i in range(15)]
        view = TaskListPaginationView(tasks=tasks, page=0)

        assert view.previous_page.disabled is True
        assert view.next_page.disabled is False

    def test_button_states_last_page(self) -> None:
        tasks = [_make_task_row(id=f"task-{i}") for i in range(15)]
        view = TaskListPaginationView(tasks=tasks, page=1)

        assert view.previous_page.disabled is False
        assert view.next_page.disabled is True


class TestAgentApprovalView:
    @pytest.mark.asyncio
    async def test_approve_calls_handler(self) -> None:
        on_approve = AsyncMock()
        view = AgentApprovalView(
            task_id="task-abc-123",
            agent_name="email",
            action_description="Send draft email",
            on_approve=on_approve,
        )
        interaction = _make_interaction()

        await view.approve.callback(interaction)

        on_approve.assert_called_once_with("task-abc-123")
        interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_calls_handler(self) -> None:
        on_reject = AsyncMock()
        view = AgentApprovalView(
            task_id="task-abc-123",
            agent_name="email",
            action_description="Send draft email",
            on_reject=on_reject,
        )
        interaction = _make_interaction()

        await view.reject.callback(interaction)

        on_reject.assert_called_once_with("task-abc-123")
