"""Unit tests for the agent activity feed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.agents.base import AgentResult
from donna.integrations.discord_agent_feed import AgentActivityFeed


def _make_bot() -> MagicMock:
    """Build a mock DonnaBot."""
    bot = MagicMock()
    bot.send_embed = AsyncMock(return_value=MagicMock())
    bot.send_message_with_view = AsyncMock(return_value=MagicMock())
    return bot


class TestAgentActivityFeed:
    @pytest.mark.asyncio
    async def test_on_agent_start_posts_embed(self) -> None:
        bot = _make_bot()
        feed = AgentActivityFeed(bot)

        await feed.on_agent_start("task-abc-123", "scheduler", "Buy milk")

        bot.send_embed.assert_called_once()
        args = bot.send_embed.call_args
        assert args[0][0] == "agents"
        embed = args[0][1]
        assert "scheduler" in embed.title
        assert "Buy milk" in embed.description

    @pytest.mark.asyncio
    async def test_on_agent_complete_posts_embed(self) -> None:
        bot = _make_bot()
        feed = AgentActivityFeed(bot)
        result = AgentResult(
            status="complete",
            output={"summary": "Scheduled for tomorrow"},
            duration_ms=1500,
        )

        await feed.on_agent_complete("task-abc-123", "scheduler", result, cost_usd=0.005)

        bot.send_embed.assert_called_once()
        embed = bot.send_embed.call_args[0][1]
        assert "complete" in embed.title.lower() or "Complete" in embed.title

    @pytest.mark.asyncio
    async def test_on_agent_complete_with_approval(self) -> None:
        bot = _make_bot()
        feed = AgentActivityFeed(bot)
        result = AgentResult(status="complete", output={})
        on_approve = AsyncMock()

        await feed.on_agent_complete(
            "task-abc-123",
            "email",
            result,
            approvable_action="Send draft email",
            on_approve=on_approve,
        )

        # Should use send_message_with_view for approvable actions.
        bot.send_message_with_view.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_agent_failure_posts_embed(self) -> None:
        bot = _make_bot()
        feed = AgentActivityFeed(bot)

        await feed.on_agent_failure("task-abc-123", "scheduler", "Timeout after 30s")

        bot.send_embed.assert_called_once()
        embed = bot.send_embed.call_args[0][1]
        assert "Failed" in embed.title
        assert "Timeout" in embed.description
