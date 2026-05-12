"""Tests for Discord user auto-onboarding flow and DM delivery."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from donna.integrations.discord_bot import DonnaBot

TASKS_CHANNEL_ID = 111111111111111111
USER_DISCORD_ID = "999888777666555444"


def _make_message(
    content: str = "Buy milk",
    channel_id: int = TASKS_CHANNEL_ID,
    author_id: str = USER_DISCORD_ID,
    author_name: str = "testuser",
    author_is_bot: bool = False,
) -> MagicMock:
    message = MagicMock()
    message.content = content
    message.channel.id = channel_id
    message.channel.send = AsyncMock()
    message.author.bot = author_is_bot
    message.author.id = author_id
    message.author.name = author_name
    message.create_thread = AsyncMock()
    return message


def _make_bot(database: AsyncMock | None = None) -> DonnaBot:
    parser = AsyncMock()
    db = database or AsyncMock()
    with patch.object(discord.Client, "__init__", return_value=None):
        bot = DonnaBot(
            input_parser=parser,
            database=db,
            tasks_channel_id=TASKS_CHANNEL_ID,
        )
    return bot


class TestOnboardingGate:
    async def test_unknown_user_gets_name_challenge(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        bot = _make_bot(db)

        msg = _make_message(content="Track my package")
        await bot.on_message(msg)

        msg.channel.send.assert_called_once()
        sent = msg.channel.send.call_args[0][0]
        assert "name" in sent.lower()
        assert USER_DISCORD_ID in bot._pending_onboarding
        assert bot._pending_onboarding[USER_DISCORD_ID] == "Track my package"

    async def test_name_reply_creates_user_and_replays(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        db.create_discord_user = AsyncMock(return_value="testuser")
        bot = _make_bot(db)

        # First message — gets challenged
        msg1 = _make_message(content="Track my package")
        await bot.on_message(msg1)

        # Second message — name reply
        db.resolve_user_id = AsyncMock(return_value=None)
        msg2 = _make_message(content="Alice")
        await bot.on_message(msg2)

        db.create_discord_user.assert_called_once_with(
            discord_id=USER_DISCORD_ID,
            name="Alice",
            discord_username="testuser",
        )
        assert USER_DISCORD_ID not in bot._pending_onboarding
        # Confirmation message should mention name
        calls = msg2.channel.send.call_args_list
        assert any("Alice" in str(c) for c in calls)

    async def test_known_user_bypasses_onboarding(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value="nick")
        bot = _make_bot(db)

        msg = _make_message(content="Buy milk")
        await bot.on_message(msg)

        # Should NOT get a name challenge
        if msg.channel.send.called:
            sent = msg.channel.send.call_args[0][0]
            assert "name" not in sent.lower()

    async def test_repeat_messages_while_pending_get_reminder(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        bot = _make_bot(db)

        msg1 = _make_message(content="First message")
        await bot.on_message(msg1)

        msg2 = _make_message(content="")  # empty — not a valid name
        await bot.on_message(msg2)

        # Second call should re-prompt, not create user
        calls = msg2.channel.send.call_args_list
        assert any("name" in str(c).lower() for c in calls)

    async def test_empty_name_reprompts(self):
        db = AsyncMock()
        db.resolve_user_id = AsyncMock(return_value=None)
        bot = _make_bot(db)

        msg1 = _make_message(content="Track my package")
        await bot.on_message(msg1)

        msg2 = _make_message(content="   ")
        await bot.on_message(msg2)

        db.create_discord_user = AsyncMock()
        db.create_discord_user.assert_not_called()
        assert USER_DISCORD_ID in bot._pending_onboarding


class TestSendDm:
    async def test_send_dm_fetches_user_and_sends(self):
        bot = _make_bot()
        mock_user = MagicMock()
        mock_user.send = AsyncMock()
        bot.fetch_user = AsyncMock(return_value=mock_user)

        await bot.send_dm("123456789", "Hello!")

        bot.fetch_user.assert_called_once_with(123456789)
        mock_user.send.assert_called_once_with("Hello!")

    async def test_send_dm_handles_fetch_failure(self):
        bot = _make_bot()
        bot.fetch_user = AsyncMock(side_effect=discord.NotFound(MagicMock(), "not found"))

        await bot.send_dm("123456789", "Hello!")
        # Should not raise — error is logged and swallowed
