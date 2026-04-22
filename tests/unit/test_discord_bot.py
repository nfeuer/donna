"""Unit tests for the Discord bot integration.

All tests mock discord.py objects and the InputParser / Database layers.
No real Discord connection or LLM calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from donna.integrations.discord_bot import LOW_CONFIDENCE_THRESHOLD, DonnaBot
from donna.orchestrator.input_parser import TaskParseResult

TASKS_CHANNEL_ID = 111111111111111111
DEBUG_CHANNEL_ID = 222222222222222222
USER_ID = "999888777666555444"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parse_result(**overrides: object) -> TaskParseResult:
    """Build a TaskParseResult with sensible defaults."""
    defaults: dict[str, object] = dict(
        title="Buy milk",
        description=None,
        domain="personal",
        priority=1,
        deadline=None,
        deadline_type="none",
        estimated_duration=15,
        recurrence=None,
        tags=["shopping"],
        prep_work_flag=False,
        agent_eligible=False,
        confidence=0.95,
    )
    defaults.update(overrides)
    return TaskParseResult(**defaults)  # type: ignore[arg-type]


def _make_task_row(**overrides: object) -> MagicMock:
    """Build a minimal TaskRow-like mock."""
    row = MagicMock()
    row.id = "task-abc-123"
    row.title = "Buy milk"
    row.domain = "personal"
    row.priority = 1
    for key, val in overrides.items():
        setattr(row, key, val)
    return row


def _make_message(
    content: str = "Buy milk",
    channel_id: int = TASKS_CHANNEL_ID,
    author_id: str = USER_ID,
    author_is_bot: bool = False,
) -> MagicMock:
    """Build a mock discord.Message."""
    message = MagicMock()
    message.content = content
    message.channel.id = channel_id
    message.channel.send = AsyncMock()
    message.author.bot = author_is_bot
    message.author.id = author_id
    return message


def _make_bot(
    input_parser: AsyncMock | None = None,
    database: AsyncMock | None = None,
) -> DonnaBot:
    """Instantiate DonnaBot without a real Discord connection."""
    parser = input_parser or AsyncMock()
    db = database or AsyncMock()
    # Patch discord.Client.__init__ so we can instantiate without a real token.
    with patch.object(discord.Client, "__init__", return_value=None):
        bot = DonnaBot(
            input_parser=parser,
            database=db,
            tasks_channel_id=TASKS_CHANNEL_ID,
            debug_channel_id=DEBUG_CHANNEL_ID,
        )
    return bot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDonnaBotOnMessage:
    async def test_happy_path_creates_task_and_confirms(self) -> None:
        parser = AsyncMock()
        parser.parse = AsyncMock(return_value=_make_parse_result())
        db = AsyncMock()
        db.create_task = AsyncMock(return_value=_make_task_row())

        bot = _make_bot(parser, db)
        message = _make_message()
        await bot.on_message(message)

        parser.parse.assert_called_once_with(
            "Buy milk", user_id=USER_ID, channel="discord"
        )
        db.create_task.assert_called_once()
        message.channel.send.assert_called_once()
        sent: str = message.channel.send.call_args[0][0]
        assert "Got it." in sent
        assert "Buy milk" in sent
        assert "pending" in sent

    async def test_ignores_bot_messages(self) -> None:
        parser = AsyncMock()
        db = AsyncMock()
        bot = _make_bot(parser, db)
        message = _make_message(author_is_bot=True)

        await bot.on_message(message)

        parser.parse.assert_not_called()
        db.create_task.assert_not_called()
        message.channel.send.assert_not_called()

    async def test_ignores_wrong_channel(self) -> None:
        parser = AsyncMock()
        db = AsyncMock()
        bot = _make_bot(parser, db)
        message = _make_message(channel_id=987654321)

        await bot.on_message(message)

        parser.parse.assert_not_called()
        db.create_task.assert_not_called()
        message.channel.send.assert_not_called()

    async def test_low_confidence_sends_clarification_no_task_created(self) -> None:
        parser = AsyncMock()
        parser.parse = AsyncMock(
            return_value=_make_parse_result(confidence=LOW_CONFIDENCE_THRESHOLD - 0.1)
        )
        db = AsyncMock()
        bot = _make_bot(parser, db)
        message = _make_message()

        await bot.on_message(message)

        db.create_task.assert_not_called()
        message.channel.send.assert_called_once()
        sent: str = message.channel.send.call_args[0][0]
        # Should ask for more detail, not confirm a task
        assert any(
            phrase in sent.lower()
            for phrase in ("not sure", "clarify", "detail", "understood")
        )

    async def test_degraded_mode_stores_raw_text_with_error_tag(self) -> None:
        parser = AsyncMock()
        parser.parse = AsyncMock(side_effect=RuntimeError("circuit breaker open"))
        db = AsyncMock()
        db.create_task = AsyncMock(return_value=_make_task_row(title="Buy milk"))
        bot = _make_bot(parser, db)
        message = _make_message()

        await bot.on_message(message)

        # Raw text should be stored as a fallback task
        db.create_task.assert_called_once()
        call_kwargs = db.create_task.call_args[1]
        assert call_kwargs["title"] == "Buy milk"
        assert "_parse_error" in call_kwargs["tags"]

        # Degraded confirmation sent to channel
        message.channel.send.assert_called_once()
        sent: str = message.channel.send.call_args[0][0]
        assert "captured" in sent.lower() or "brain" in sent.lower()

    async def test_degraded_mode_still_replies_if_db_also_fails(self) -> None:
        parser = AsyncMock()
        parser.parse = AsyncMock(side_effect=RuntimeError("api down"))
        db = AsyncMock()
        db.create_task = AsyncMock(side_effect=RuntimeError("db gone too"))
        bot = _make_bot(parser, db)
        message = _make_message()

        # Should not raise — degraded reply is always attempted
        await bot.on_message(message)

        message.channel.send.assert_called_once()

    async def test_correlation_id_bound_to_structlog(self) -> None:
        parser = AsyncMock()
        parser.parse = AsyncMock(return_value=_make_parse_result())
        db = AsyncMock()
        db.create_task = AsyncMock(return_value=_make_task_row())
        bot = _make_bot(parser, db)
        message = _make_message()

        with patch("donna.integrations.discord_bot.logger") as mock_logger:
            bound = MagicMock()
            bound.info = MagicMock()
            bound.exception = MagicMock()
            mock_logger.bind = MagicMock(return_value=bound)

            await bot.on_message(message)

            mock_logger.bind.assert_called_once()
            bind_kwargs = mock_logger.bind.call_args[1]
            assert "correlation_id" in bind_kwargs
            assert bind_kwargs["user_id"] == USER_ID
            assert bind_kwargs["channel"] == "discord"

    async def test_deadline_parsed_from_iso_string(self) -> None:
        """A deadline string from the parser is converted to datetime for the DB."""
        parser = AsyncMock()
        parser.parse = AsyncMock(
            return_value=_make_parse_result(
                deadline="2026-03-25T17:00:00",
                deadline_type="hard",
            )
        )
        db = AsyncMock()
        db.create_task = AsyncMock(return_value=_make_task_row())
        bot = _make_bot(parser, db)
        message = _make_message()

        await bot.on_message(message)

        call_kwargs = db.create_task.call_args[1]
        from datetime import datetime
        assert isinstance(call_kwargs["deadline"], datetime)
        assert call_kwargs["deadline"].year == 2026

    async def test_unknown_domain_falls_back_to_personal(self) -> None:
        """An unrecognised domain string should not crash — defaults to personal."""
        parser = AsyncMock()
        parser.parse = AsyncMock(
            return_value=_make_parse_result(domain="universe")
        )
        db = AsyncMock()
        db.create_task = AsyncMock(return_value=_make_task_row())
        bot = _make_bot(parser, db)
        message = _make_message()

        await bot.on_message(message)

        from donna.tasks.db_models import TaskDomain
        call_kwargs = db.create_task.call_args[1]
        assert call_kwargs["domain"] == TaskDomain.PERSONAL
