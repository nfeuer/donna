"""Tests for Discord chat channel integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.chat.types import ChatResponse


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.handle_message.return_value = ChatResponse(
        text="You have 3 tasks today.",
        suggested_actions=["schedule_task"],
    )
    engine.handle_escalation.return_value = ChatResponse(
        text="Here's a detailed plan.",
    )
    return engine


class TestDiscordChatRouting:
    def test_chat_channel_message_routes_to_engine(self, mock_engine: AsyncMock) -> None:
        """Messages in #donna-chat should route to the ConversationEngine."""
        from donna.integrations.discord_bot import DonnaBot

        bot = DonnaBot(
            input_parser=AsyncMock(),
            database=AsyncMock(),
            tasks_channel_id=111,
            chat_channel_id=222,
            chat_engine=mock_engine,
        )

        message = MagicMock()
        message.author.bot = False
        message.channel.id = 222
        message.content = "What's on my schedule?"
        message.author.id = 12345
        message.channel.send = AsyncMock()

        asyncio.get_event_loop().run_until_complete(bot.on_message(message))

        mock_engine.handle_message.assert_called_once()
        message.channel.send.assert_called_once_with("You have 3 tasks today.")

    def test_tasks_channel_still_works(self, mock_engine: AsyncMock) -> None:
        """Messages in #donna-tasks should still go through InputParser."""
        from donna.integrations.discord_bot import DonnaBot

        mock_parser = AsyncMock()
        mock_parser.parse.return_value = MagicMock(
            confidence=0.9, title="Test task", description=None,
            domain="personal", priority=2, deadline=None,
            deadline_type="none", estimated_duration=30,
            recurrence=None, tags=[], prep_work_flag=False,
            agent_eligible=False,
        )
        mock_db = AsyncMock()
        mock_db.create_task.return_value = MagicMock(
            id="t1", title="Test task", domain="personal",
            priority=2,
        )

        bot = DonnaBot(
            input_parser=mock_parser,
            database=mock_db,
            tasks_channel_id=111,
            chat_channel_id=222,
            chat_engine=mock_engine,
        )

        message = MagicMock()
        message.author.bot = False
        message.channel.id = 111
        message.content = "Buy groceries"
        message.author.id = 12345
        message.channel.send = AsyncMock()

        asyncio.get_event_loop().run_until_complete(bot.on_message(message))

        # Should go through parser, NOT the chat engine
        mock_parser.parse.assert_called_once()
        mock_engine.handle_message.assert_not_called()

    def test_escalation_shows_buttons(self, mock_engine: AsyncMock) -> None:
        """When engine returns needs_escalation, show Approve/Decline buttons."""
        from donna.integrations.discord_bot import DonnaBot

        mock_engine.handle_message.return_value = ChatResponse(
            text="I'd need Claude for this — complex planning. ~$0.03. Go ahead?",
            needs_escalation=True,
            escalation_reason="Complex planning",
            estimated_cost=0.03,
        )

        bot = DonnaBot(
            input_parser=AsyncMock(),
            database=AsyncMock(),
            tasks_channel_id=111,
            chat_channel_id=222,
            chat_engine=mock_engine,
        )

        message = MagicMock()
        message.author.bot = False
        message.channel.id = 222
        message.content = "Should I take on this new project?"
        message.author.id = 12345
        message.channel.send = AsyncMock()

        asyncio.get_event_loop().run_until_complete(bot.on_message(message))

        # Should be called with a view (buttons)
        send_call = message.channel.send.call_args
        assert send_call is not None
        # Check that view kwarg was passed (escalation buttons)
        assert "view" in send_call.kwargs or len(send_call.args) > 1
