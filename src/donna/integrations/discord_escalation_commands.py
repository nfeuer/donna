"""Slash commands for manual-escalation surfaces (slice 21).

Currently ships ``/donna submit <correlation_id> --branch <name>
[--sha <sha>]`` — the Discord fallback for marking a claude_code
escalation as built when the user can't reach the dashboard.

Both this command and the dashboard's ``POST /admin/escalations/<id>/submit``
route delegate to :func:`donna.cost.escalation_submit.submit_escalation_core`
so payload validation, iteration cap, and concurrent-submission
guards are identical across surfaces.

Realizes docs/superpowers/specs/manual-escalation.md §5.3 (claude_code
mode user→Donna handoff alternative path) and §10.3 row 2 (branch-not-
found feedback also flows through here on the next poller tick).

Lives in a separate module from ``discord_commands.py`` to keep
slice-specific surfaces independent and minimise rebase pain when
slice 20 also touches ``discord_commands.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import discord
import structlog
from discord import Interaction, app_commands

from donna.cost.escalation_submit import (
    ConcurrentSubmissionError,
    IterationCapReachedError,
    ModeMismatchError,
    NotAwaitingSubmissionError,
    NotFoundError,
    SchemaValidationError,
    submit_escalation_core,
)

if TYPE_CHECKING:
    from donna.integrations.discord_bot import DonnaBot

logger = structlog.get_logger()


# Spec §10.3 row 2 — slash command should reject a non-claude_code
# row early so the user gets a clear error instead of "mode_mismatch".
_SLASH_COMMAND_SUPPORTED_MODES = ("claude_code",)


def register_escalation_commands(
    bot: DonnaBot,
    *,
    conn: aiosqlite.Connection,
    owner_discord_id: int | None = None,
) -> None:
    """Register the ``/donna submit`` slash command on the bot.

    Args:
        bot: Donna's discord.py bot instance.
        conn: Shared aiosqlite connection.
        owner_discord_id: When set, restricts the command to the owner
            (matches the slice 17 BudgetEscalationView gate).
    """
    guild = discord.Object(id=bot._guild_id) if bot._guild_id else None

    @bot.tree.command(
        name="submit",
        description="Submit a manual claude_code escalation (slice 21).",
        guild=guild,
    )
    @app_commands.describe(
        correlation_id="Escalation correlation_id from the Discord ping.",
        branch="Branch name carrying your build (e.g. escalation/abcd1234-foo).",
        sha="(optional) Commit SHA at the branch tip — locks the validation "
        "to that SHA per spec §10.3 row 4.",
    )
    async def submit_cmd(
        interaction: Interaction,
        correlation_id: str,
        branch: str,
        sha: str | None = None,
    ) -> None:
        if (
            owner_discord_id is not None
            and interaction.user.id != owner_discord_id
        ):
            await interaction.response.send_message(
                "Only the account owner can submit escalations.",
                ephemeral=True,
            )
            return

        # Pre-flight check the mode so the user sees a clear error
        # rather than the schema-discriminator mismatch from
        # submit_escalation_core. The full guard is still in
        # submit_escalation_core; this is just a UX nicety.
        cursor = await conn.execute(
            "SELECT mode FROM escalation_request WHERE correlation_id = ?",
            (correlation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            await interaction.response.send_message(
                f"No escalation matches `{correlation_id}`.",
                ephemeral=True,
            )
            return
        existing_mode = row[0]
        if (
            existing_mode is not None
            and existing_mode not in _SLASH_COMMAND_SUPPORTED_MODES
        ):
            await interaction.response.send_message(
                f"Mode `{existing_mode}` is not supported by `/donna submit` — "
                "use the dashboard for chat-mode submissions.",
                ephemeral=True,
            )
            return

        payload: dict[str, str] = {
            "mode": "claude_code",
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        try:
            result = await submit_escalation_core(
                conn, correlation_id, payload
            )
        except SchemaValidationError as exc:
            await interaction.response.send_message(
                f"Submission rejected: {exc.message}",
                ephemeral=True,
            )
            return
        except NotFoundError:
            await interaction.response.send_message(
                f"No escalation matches `{correlation_id}`.",
                ephemeral=True,
            )
            return
        except NotAwaitingSubmissionError as exc:
            await interaction.response.send_message(
                f"Escalation is in `{exc.status}` state — can't accept a "
                "submission right now.",
                ephemeral=True,
            )
            return
        except ModeMismatchError as exc:
            await interaction.response.send_message(
                f"Mode mismatch: row is `{exc.expected}`, you sent "
                f"`{exc.submitted}`.",
                ephemeral=True,
            )
            return
        except IterationCapReachedError as exc:
            await interaction.response.send_message(
                f"Iteration cap reached ({exc.iteration}/{exc.limit}). "
                "This escalation has been routed to human review — see the "
                "dashboard.",
                ephemeral=True,
            )
            return
        except ConcurrentSubmissionError:
            await interaction.response.send_message(
                "Another submission was just accepted for this escalation. "
                "Refresh the dashboard to see the latest state.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                f"Submitted `{result.correlation_id}` as `{result.mode}` "
                f"(iteration {result.iteration}). "
                f"The poller will pick this up within a minute."
            ),
            ephemeral=True,
        )
        logger.info(
            "donna_submit_slash_command",
            correlation_id=result.correlation_id,
            branch=branch,
            sha=sha,
            iteration=result.iteration,
            user_id=interaction.user.id,
        )
