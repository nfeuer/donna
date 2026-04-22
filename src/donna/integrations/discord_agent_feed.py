"""Agent activity feed — posts agent start/complete/failure to #donna-agents.

Implements the AgentActivityListener protocol so the AgentDispatcher
can notify Discord of all agent activity. Approvable actions (e.g., email
drafts) are posted with approval buttons.

See the discord interaction expansion plan.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
import structlog

from donna.agents.base import AgentResult
from donna.integrations.discord_views import AgentApprovalView

if TYPE_CHECKING:
    from donna.integrations.discord_bot import DonnaBot

logger = structlog.get_logger()

EMBED_COLOUR_START = 0x3498DB    # Blue
EMBED_COLOUR_COMPLETE = 0x2ECC71  # Green
EMBED_COLOUR_FAILURE = 0xE74C3C   # Red


class AgentActivityFeed:
    """Posts agent activity to #donna-agents channel."""

    def __init__(self, bot: DonnaBot) -> None:
        self._bot = bot

    async def on_agent_start(
        self, task_id: str, agent_name: str, task_title: str
    ) -> None:
        """Post an embed when an agent starts working on a task."""
        embed = discord.Embed(
            title=f"Agent Started: {agent_name}",
            description=f"Working on: **{task_title}**",
            colour=EMBED_COLOUR_START,
        )
        embed.add_field(name="Task ID", value=task_id[:8], inline=True)
        embed.set_footer(text=f"Agent: {agent_name}")

        await self._bot.send_embed("agents", embed)
        logger.info(
            "agent_feed_start",
            task_id=task_id,
            agent=agent_name,
        )

    async def on_agent_complete(
        self,
        task_id: str,
        agent_name: str,
        result: AgentResult,
        cost_usd: float = 0.0,
        approvable_action: str | None = None,
        on_approve: Callable[[str], Awaitable[None]] | None = None,
        on_reject: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Post an embed when an agent completes, with optional approval buttons."""
        embed = discord.Embed(
            title=f"Agent Complete: {agent_name}",
            description=f"Status: **{result.status}**",
            colour=EMBED_COLOUR_COMPLETE,
        )
        embed.add_field(name="Task ID", value=task_id[:8], inline=True)
        if result.duration_ms:
            embed.add_field(
                name="Duration", value=f"{result.duration_ms}ms", inline=True
            )
        if cost_usd > 0:
            embed.add_field(name="Cost", value=f"${cost_usd:.4f}", inline=True)
        if result.tool_calls_made:
            embed.add_field(
                name="Tool Calls",
                value=str(len(result.tool_calls_made)),
                inline=True,
            )

        # Summarise output if present.
        output_summary = ""
        if isinstance(result.output, dict):
            for key in ("summary", "result", "message", "digest_text"):
                if key in result.output:
                    output_summary = str(result.output[key])[:500]
                    break
        if output_summary:
            embed.add_field(
                name="Output", value=output_summary, inline=False
            )

        if approvable_action and (on_approve or on_reject):
            view = AgentApprovalView(
                task_id=task_id,
                agent_name=agent_name,
                action_description=approvable_action,
                on_approve=on_approve,
                on_reject=on_reject,
            )
            await self._bot.send_message_with_view(
                "agents", "", view=view, embed=embed
            )
        else:
            await self._bot.send_embed("agents", embed)

        logger.info(
            "agent_feed_complete",
            task_id=task_id,
            agent=agent_name,
            status=result.status,
            cost_usd=cost_usd,
        )

    async def on_agent_failure(
        self, task_id: str, agent_name: str, error: str
    ) -> None:
        """Post an embed when an agent fails."""
        embed = discord.Embed(
            title=f"Agent Failed: {agent_name}",
            description=f"Error: {error[:1000]}",
            colour=EMBED_COLOUR_FAILURE,
        )
        embed.add_field(name="Task ID", value=task_id[:8], inline=True)

        await self._bot.send_embed("agents", embed)
        logger.info(
            "agent_feed_failure",
            task_id=task_id,
            agent=agent_name,
            error=error[:200],
        )
