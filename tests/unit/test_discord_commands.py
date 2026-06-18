"""Unit tests for Discord slash commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.agents.decomposition import DecomposeResult
from donna.integrations.discord_commands import (
    _build_breakdown_embed,
    _dependency_positions,
    _handle_breakdown,
    _parse_when,
    _task_autocomplete,
    register_commands,
)


def _make_task_row(**overrides: object) -> MagicMock:
    """Build a minimal TaskRow-like mock."""
    row = MagicMock()
    row.id = "task-abc-12345678"
    row.title = "Buy milk"
    row.description = "From the store"
    row.domain = "personal"
    row.priority = 2
    row.status = "backlog"
    row.scheduled_start = None
    row.estimated_duration = 30
    row.notes = None
    row.created_at = "2024-04-01T10:00:00"
    row.completed_at = None
    row.actual_start = None
    row.reschedule_count = 0
    for key, val in overrides.items():
        setattr(row, key, val)
    return row


def _make_bot() -> MagicMock:
    """Build a mock DonnaBot for command registration."""
    bot = MagicMock()
    bot._guild_id = 123456789
    bot.tree = MagicMock()
    # Capture registered commands.
    bot.tree.command = MagicMock(side_effect=lambda **kwargs: lambda fn: fn)
    return bot


class TestParseWhen:
    def test_iso_format(self) -> None:
        result = _parse_when("2024-04-05 14:00")
        assert result is not None
        assert result.hour == 14
        assert result.day == 5

    def test_iso_format_with_t(self) -> None:
        result = _parse_when("2024-04-05T14:00")
        assert result is not None
        assert result.hour == 14

    def test_relative_hours(self) -> None:
        before = datetime.now(tz=UTC)
        result = _parse_when("+2h")
        assert result is not None
        assert result >= before + timedelta(hours=1, minutes=59)

    def test_relative_minutes(self) -> None:
        before = datetime.now(tz=UTC)
        result = _parse_when("+30m")
        assert result is not None
        assert result >= before + timedelta(minutes=29)

    def test_tomorrow_pm(self) -> None:
        result = _parse_when("tomorrow 2pm")
        assert result is not None
        assert result.hour == 14
        now = datetime.now(tz=UTC)
        assert result.date() == (now + timedelta(days=1)).date()

    def test_today_am(self) -> None:
        result = _parse_when("today 10am")
        assert result is not None
        assert result.hour == 10

    def test_invalid_returns_none(self) -> None:
        assert _parse_when("next tuesday maybe") is None
        assert _parse_when("") is None


class TestTaskAutocomplete:
    @pytest.mark.asyncio
    async def test_returns_active_tasks(self) -> None:
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[
            _make_task_row(id="task-1", title="Task one", status="backlog"),
            _make_task_row(id="task-2", title="Task two", status="done"),
            _make_task_row(id="task-3", title="Task three", status="scheduled"),
        ])
        interaction = MagicMock()

        choices = await _task_autocomplete(interaction, "", db)

        # "done" tasks should be excluded.
        ids = [c.value for c in choices]
        assert "task-1" in ids
        assert "task-3" in ids
        assert "task-2" not in ids

    @pytest.mark.asyncio
    async def test_filters_by_current_text(self) -> None:
        db = AsyncMock()
        db.list_tasks = AsyncMock(return_value=[
            _make_task_row(id="task-1", title="Buy milk", status="backlog"),
            _make_task_row(id="task-2", title="Fix bug", status="backlog"),
        ])
        interaction = MagicMock()

        choices = await _task_autocomplete(interaction, "milk", db)

        assert len(choices) == 1
        assert choices[0].value == "task-1"


class TestRegisterCommands:
    def test_registers_all_commands(self) -> None:
        """Verify register_commands doesn't raise and registers commands on tree."""
        bot = MagicMock()
        bot._guild_id = 123456789
        bot.tree = MagicMock()

        # Make bot.tree.command return a decorator that returns an object
        # with an .autocomplete method (mimicking real app_commands.Command).
        registered = []

        def mock_command(**kwargs):
            def decorator(fn):
                registered.append(kwargs.get("name"))
                # Return a mock that has .autocomplete and .describe
                cmd = MagicMock()
                cmd.autocomplete = MagicMock(return_value=lambda fn: fn)
                return cmd
            return decorator

        bot.tree.command = mock_command
        db = AsyncMock()

        register_commands(bot, db, "nick")

        expected = {
            "tasks", "done", "cancel", "reschedule", "next",
            "today", "tomorrow", "edit", "status",
        }
        assert set(registered) == expected

    def test_breakdown_registered_only_with_service(self) -> None:
        """/breakdown appears iff a decomposition service is injected."""

        def make_bot_capturing(registered: list[str]) -> MagicMock:
            bot = MagicMock()
            bot._guild_id = 123456789
            bot.tree = MagicMock()

            def mock_command(**kwargs):
                def decorator(fn):
                    registered.append(kwargs.get("name"))
                    cmd = MagicMock()
                    cmd.autocomplete = MagicMock(return_value=lambda fn: fn)
                    return cmd
                return decorator

            bot.tree.command = mock_command
            return bot

        db = AsyncMock()

        without: list[str] = []
        register_commands(make_bot_capturing(without), db, "nick")
        assert "breakdown" not in without

        with_svc: list[str] = []
        register_commands(
            make_bot_capturing(with_svc), db, "nick",
            decomposition_service=MagicMock(),
        )
        assert "breakdown" in with_svc


def _make_interaction() -> MagicMock:
    """Interaction mock supporting defer / send_message / followup.send."""
    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


class TestHandleBreakdown:
    @pytest.mark.asyncio
    async def test_task_not_found_does_not_defer_or_decompose(self) -> None:
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=None)
        service = MagicMock()
        service.decompose = AsyncMock()
        interaction = _make_interaction()

        await _handle_breakdown(
            interaction=interaction, task_id="missing", db=db, service=service,
        )

        interaction.response.send_message.assert_awaited_once()
        assert "not found" in interaction.response.send_message.await_args.args[0].lower()
        interaction.response.defer.assert_not_awaited()
        service.decompose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_defers_and_followups_with_embed(self) -> None:
        parent = _make_task_row(id="task-1", title="Launch the thing")
        sub1 = _make_task_row(id="sub-1", title="Research", estimated_duration=60,
                              dependencies=None)
        sub2 = _make_task_row(id="sub-2", title="Build", estimated_duration=90,
                              dependencies=json.dumps(["sub-1"]))
        db = AsyncMock()
        db.get_task = AsyncMock(side_effect=[parent, sub1, sub2])

        service = MagicMock()
        service.decompose = AsyncMock(return_value=DecomposeResult(
            parent_task_id="task-1",
            subtask_ids=["sub-1", "sub-2"],
            total_estimated_hours=2.5,
            missing_information=[],
            deadline_feasible=True,
        ))
        interaction = _make_interaction()

        await _handle_breakdown(
            interaction=interaction, task_id="task-1", db=db, service=service,
        )

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.await_args.kwargs["embed"]
        assert "Launch the thing" in embed.title
        # Dependency rendered as a back-reference to the first subtask.
        assert "after #1" in embed.description

    @pytest.mark.asyncio
    async def test_empty_decomposition_reports_atomic(self) -> None:
        parent = _make_task_row(id="task-1", title="Tiny task")
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=parent)
        service = MagicMock()
        service.decompose = AsyncMock(return_value=DecomposeResult(
            parent_task_id="task-1", subtask_ids=[], total_estimated_hours=0.0,
            missing_information=[], deadline_feasible=None,
        ))
        interaction = _make_interaction()

        await _handle_breakdown(
            interaction=interaction, task_id="task-1", db=db, service=service,
        )

        interaction.followup.send.assert_awaited_once()
        assert "atomic" in interaction.followup.send.await_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_decompose_error_replies_gracefully(self) -> None:
        parent = _make_task_row(id="task-1", title="Boom")
        db = AsyncMock()
        db.get_task = AsyncMock(return_value=parent)
        service = MagicMock()
        service.decompose = AsyncMock(side_effect=RuntimeError("llm down"))
        interaction = _make_interaction()

        await _handle_breakdown(
            interaction=interaction, task_id="task-1", db=db, service=service,
        )

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        assert "couldn't" in interaction.followup.send.await_args.args[0].lower()


class TestBreakdownEmbedHelpers:
    def test_dependency_positions_maps_uuids_to_positions(self) -> None:
        sub = _make_task_row(id="sub-2", dependencies=json.dumps(["sub-1", "sub-3"]))
        pos_by_id = {"sub-1": 1, "sub-2": 2, "sub-3": 3}
        assert _dependency_positions(sub, pos_by_id) == [1, 3]

    def test_dependency_positions_handles_no_or_bad_deps(self) -> None:
        assert _dependency_positions(_make_task_row(dependencies=None), {}) == []
        assert _dependency_positions(_make_task_row(dependencies="not-json"), {}) == []

    def test_embed_footer_and_open_questions(self) -> None:
        parent = _make_task_row(id="task-1", title="Plan trip")
        sub = _make_task_row(id="sub-1", title="Book flights",
                            estimated_duration=30, dependencies=None)
        result = DecomposeResult(
            parent_task_id="task-1", subtask_ids=["sub-1"],
            total_estimated_hours=1.0,
            missing_information=[{"question": "Which dates?", "blocking": True}],
            deadline_feasible=False,
        )
        embed = _build_breakdown_embed(parent=parent, subtasks=[sub], result=result)
        assert "1 subtask(s) created" in embed.footer.text
        field_names = [f.name for f in embed.fields]
        assert any("Open questions" in n for n in field_names)
        assert any("Deadline concern" in n for n in field_names)
