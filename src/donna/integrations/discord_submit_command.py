"""Discord slash command ``/donna submit`` for chat-mode escalation answers.

Realizes ``docs/superpowers/specs/manual-escalation.md`` §5.2 (Discord
fallback path) and §10.3 row 1 (slash-command validation).

The dashboard textarea is the canonical submission surface. This slash
command is the on-the-go fallback for short answers when the user can't
get to a browser. It enforces:

- A min length matching :func:`schemas/escalation_submission.json`
  (50 chars).
- A hard max length (default 3000) below Discord's per-option ceiling
  so longer answers are explicitly redirected to the dashboard.
- Owner-id check (only the configured ``OWNER_DISCORD_ID`` may submit).
- The same shared validation + audit path as the HTTP endpoint via
  :func:`donna.cost.escalation_submit_service.apply_submission`.

The command is registered by :func:`register_submit_command` from the
slash-command wiring in :mod:`donna.integrations.discord_commands`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import Interaction, app_commands

from donna.config import PromptDeliveryConfig
from donna.cost.escalation_submit_service import (
    SubmissionError,
    apply_submission,
)

if TYPE_CHECKING:
    import aiosqlite

    from donna.integrations.discord_bot import DonnaBot

logger = structlog.get_logger()


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------


def register_submit_command(
    *,
    bot: DonnaBot,
    conn: aiosqlite.Connection,
    config: PromptDeliveryConfig,
    iteration_limit: int,
    owner_discord_id: int | None,
) -> None:
    """Register the ``/donna_submit`` slash command on the bot's tree.

    Discord slash commands cannot have spaces in their names, so the
    spec's ``/donna submit`` reads as ``/donna_submit`` on the wire.
    The command is guild-scoped (mirrors the rest of
    :mod:`donna.integrations.discord_commands`) so changes propagate
    instantly to the configured guild without the global-sync delay.

    All validation happens before the row is touched; on success we
    delegate to :func:`apply_submission`, sharing the exact code path
    with ``POST /admin/escalations/{cid}/submit``.
    """
    if bot.tree is None:
        logger.warning("discord_submit_command_no_tree")
        return

    guild = discord.Object(id=bot._guild_id) if bot._guild_id else None

    @bot.tree.command(
        name="donna_submit",
        description="Submit a chat-mode escalation answer (short answers only).",
        guild=guild,
    )
    @app_commands.describe(
        correlation_id="The correlation ID from the escalation Discord post.",
        answer="Your answer text. Use the dashboard for long answers.",
    )
    async def submit_cmd(
        interaction: Interaction,
        correlation_id: str,
        answer: str,
    ) -> None:
        await _handle_submit(
            interaction=interaction,
            correlation_id=correlation_id,
            answer=answer,
            conn=conn,
            config=config,
            iteration_limit=iteration_limit,
            owner_discord_id=owner_discord_id,
        )


async def _handle_submit(
    *,
    interaction: Interaction,
    correlation_id: str,
    answer: str,
    conn: aiosqlite.Connection,
    config: PromptDeliveryConfig,
    iteration_limit: int,
    owner_discord_id: int | None,
) -> None:
    """Validate + dispatch a single ``/donna_submit`` invocation.

    Pulled out of the registration closure so unit tests can exercise
    the validation matrix without spinning up a Discord client.
    """
    user_id = interaction.user.id

    # Owner check first — refuse anyone else even before length checks
    # so we don't reveal correlation_id existence to non-owners.
    if owner_discord_id is not None and user_id != owner_discord_id:
        logger.warning(
            "donna_submit_owner_mismatch",
            actual_user_id=user_id,
            expected_user_id=owner_discord_id,
        )
        await interaction.response.send_message(
            "Only the account owner can submit escalation answers.",
            ephemeral=True,
        )
        return

    # Slash arg length cap (spec §10.3 row 1).
    answer = answer.strip()
    if len(answer) < config.chat_min_answer_chars:
        await interaction.response.send_message(
            (
                f"Answer is too short — paste at least "
                f"{config.chat_min_answer_chars} characters."
            ),
            ephemeral=True,
        )
        return
    if len(answer) > config.slash_command_max_chars:
        await interaction.response.send_message(
            (
                f"Answer is too long for a slash command "
                f"(>{config.slash_command_max_chars} chars). "
                "Use the dashboard for long answers."
            ),
            ephemeral=True,
        )
        return

    payload = {"mode": "chat", "answer": answer}

    try:
        result = await apply_submission(
            conn=conn,
            correlation_id=correlation_id,
            payload=payload,
            iteration_limit=iteration_limit,
        )
    except SubmissionError as exc:
        message = _humanize_submission_error(exc)
        logger.info(
            "donna_submit_rejected",
            correlation_id=correlation_id,
            code=exc.code,
            user_id=user_id,
        )
        await interaction.response.send_message(message, ephemeral=True)
        return
    except Exception:
        logger.exception(
            "donna_submit_unexpected_failure",
            correlation_id=correlation_id,
            user_id=user_id,
        )
        await interaction.response.send_message(
            "Couldn't submit your answer — try again or use the dashboard.",
            ephemeral=True,
        )
        return

    logger.info(
        "donna_submit_accepted",
        correlation_id=correlation_id,
        user_id=user_id,
        iteration=result.iteration,
    )
    await interaction.response.send_message(
        (
            f"Submitted ({result.iteration} of "
            f"{iteration_limit} iterations) — "
            "Donna will fold the answer into the originating task."
        ),
        ephemeral=True,
    )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _humanize_submission_error(exc: SubmissionError) -> str:
    """Map a :class:`SubmissionError` ``code`` to a Discord-friendly string."""
    if exc.code == "not_found":
        return "No escalation matches that correlation ID."
    if exc.code == "schema_validation_failed":
        return f"Answer didn't pass validation: {exc.message or 'invalid shape'}."
    if exc.code == "not_awaiting_submission":
        status = exc.extras.get("status", "unknown")
        return f"This escalation isn't awaiting a submission (current status: {status})."
    if exc.code == "mode_mismatch":
        expected = exc.extras.get("expected_mode", "?")
        return (
            f"This escalation was opened as `{expected}` mode — "
            "use the dashboard for the matching mode."
        )
    if exc.code == "iteration_cap_reached":
        limit = exc.extras.get("limit", "?")
        return (
            f"You've hit the iteration cap ({limit}). "
            "Donna escalated this for human review."
        )
    if exc.code == "concurrent_submission":
        return "Another submission landed first — refresh the dashboard."
    return f"Submission rejected: {exc.code}."


__all__ = ["register_submit_command"]
