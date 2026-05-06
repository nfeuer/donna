"""Unit tests for ToolGapPingView (slice 22)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.cost.tool_request_repository import ToolRequestRow
from donna.integrations.discord_views import ToolGapPingView


def _row(*, id_=42, status="open") -> ToolRequestRow:
    now = datetime.now(tz=UTC)
    return ToolRequestRow(
        id=id_,
        user_id="nick",
        tool_name="web_fetch",
        proposed_signature=None,
        rationale="cap blocked",
        blocking_capability_id="news_check",
        priority=3,
        status=status,
        severity="high",
        detection_point="scheduler_pre_run",
        snoozed_until=None,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        resolved_at=None,
        resolved_branch=None,
        escalation_request_id=None,
        last_pinged_at=None,
    )


def _fake_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


def _gate_with_open_method() -> MagicMock:
    gate = MagicMock()
    esc_row = MagicMock()
    esc_row.id = 100
    esc_row.correlation_id = "corr-abc"
    gate.open_tool_build_escalation = AsyncMock(return_value=(esc_row, None))
    return gate


@pytest.mark.asyncio
async def test_file_request_creates_escalation_and_marks_in_progress():
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=_row())
    repo.mark_in_progress = AsyncMock(return_value=True)
    gate = _gate_with_open_method()
    view = ToolGapPingView(
        tool_request_id=42,
        tool_name="web_fetch",
        owner_discord_id=999,
        gate=gate,
        tool_request_repo=repo,
    )
    file_button = view.children[0]
    interaction = _fake_interaction(user_id=999)
    await file_button.callback(interaction)

    gate.open_tool_build_escalation.assert_awaited_once()
    repo.mark_in_progress.assert_awaited_once()
    interaction.response.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_owner_mismatch_rejects_file_request():
    repo = AsyncMock()
    gate = _gate_with_open_method()
    view = ToolGapPingView(
        tool_request_id=42,
        tool_name="web_fetch",
        owner_discord_id=999,
        gate=gate,
        tool_request_repo=repo,
    )
    file_button = view.children[0]
    interaction = _fake_interaction(user_id=12345)  # not owner
    await file_button.callback(interaction)
    interaction.response.send_message.assert_awaited()
    gate.open_tool_build_escalation.assert_not_called()


@pytest.mark.asyncio
async def test_stale_click_when_already_in_progress():
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=_row(status="in_progress"))
    gate = _gate_with_open_method()
    view = ToolGapPingView(
        tool_request_id=42,
        tool_name="web_fetch",
        owner_discord_id=999,
        gate=gate,
        tool_request_repo=repo,
    )
    file_button = view.children[0]
    interaction = _fake_interaction(user_id=999)
    await file_button.callback(interaction)
    gate.open_tool_build_escalation.assert_not_called()


@pytest.mark.asyncio
async def test_snooze_button_calls_repo_snooze():
    repo = AsyncMock()
    repo.snooze = AsyncMock(return_value=True)
    gate = _gate_with_open_method()
    view = ToolGapPingView(
        tool_request_id=42,
        tool_name="web_fetch",
        owner_discord_id=999,
        gate=gate,
        tool_request_repo=repo,
        snooze_seconds=60 * 60 * 24,
    )
    snooze_button = view.children[1]
    interaction = _fake_interaction(user_id=999)
    await snooze_button.callback(interaction)
    repo.snooze.assert_awaited_once_with(42, seconds=86400)


@pytest.mark.asyncio
async def test_snooze_owner_mismatch_rejects():
    repo = AsyncMock()
    gate = _gate_with_open_method()
    view = ToolGapPingView(
        tool_request_id=42,
        tool_name="web_fetch",
        owner_discord_id=999,
        gate=gate,
        tool_request_repo=repo,
    )
    snooze_button = view.children[1]
    interaction = _fake_interaction(user_id=12345)
    await snooze_button.callback(interaction)
    repo.snooze.assert_not_called()


@pytest.mark.asyncio
async def test_snooze_returns_false_for_stale_row():
    repo = AsyncMock()
    repo.snooze = AsyncMock(return_value=False)
    gate = _gate_with_open_method()
    view = ToolGapPingView(
        tool_request_id=42,
        tool_name="web_fetch",
        owner_discord_id=999,
        gate=gate,
        tool_request_repo=repo,
    )
    snooze_button = view.children[1]
    interaction = _fake_interaction(user_id=999)
    await snooze_button.callback(interaction)
    interaction.response.send_message.assert_awaited()
