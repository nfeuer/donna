"""Unit tests for Discord slash commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from donna.integrations.discord_commands import (
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
        before = datetime.now(tz=timezone.utc)
        result = _parse_when("+2h")
        assert result is not None
        assert result >= before + timedelta(hours=1, minutes=59)

    def test_relative_minutes(self) -> None:
        before = datetime.now(tz=timezone.utc)
        result = _parse_when("+30m")
        assert result is not None
        assert result >= before + timedelta(minutes=29)

    def test_tomorrow_pm(self) -> None:
        result = _parse_when("tomorrow 2pm")
        assert result is not None
        assert result.hour == 14
        now = datetime.now(tz=timezone.utc)
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

        expected = {"tasks", "done", "cancel", "reschedule", "next", "today", "tomorrow", "edit", "status"}
        assert set(registered) == expected
