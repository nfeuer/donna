"""Slice 20 tests for the chat-mode delivery callback enrichment.

Realizes the verification leg of
``docs/superpowers/specs/manual-escalation.md`` §5.2 / §10.2 row 2:
when the row carries a chat-mode summary + prompt path, the Discord
notification body uses the summary, the workspace .md is attached,
and a failed attachment upload still posts the message and logs
``attachment_upload_failed``.

We exercise the message-body and attachment helpers directly rather
than spinning up a discord client. The deliver closure is tested by
calling it with a hand-rolled row + a mocked bot.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.cli_wiring import (
    _build_attachment,
    _build_escalation_message_body,
    _make_escalation_delivery_callback,
)
from donna.config import PromptDeliveryConfig


def _row(**overrides) -> SimpleNamespace:
    base = {
        "correlation_id": "cid-1",
        "task_type": "chat_escalation",
        "task_id": "task-1",
        "estimate_usd": 7.5,
        "daily_remaining_usd": 1.0,
        "offered_modes": ["chat", "pause", "cancel"],
        "summary": "Summary line goes here.",
        "prompt_path": None,
        "mode": "chat",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------
# _build_escalation_message_body
# ---------------------------------------------------------------------


class TestMessageBody:
    def test_includes_summary_for_chat_mode(self) -> None:
        body = _build_escalation_message_body(
            row=_row(summary="Summary text"), host_base_url="https://example.com"
        )
        assert "Summary text" in body
        assert "cid-1" in body
        assert "Estimate: $7.50" in body
        assert "https://example.com/admin/escalations/cid-1" in body

    def test_falls_back_to_legacy_text_when_no_summary(self) -> None:
        body = _build_escalation_message_body(
            row=_row(summary=None, offered_modes=["pause", "cancel"]),
            host_base_url="",
        )
        assert "Over-budget decision" in body
        assert "pause, or cancel" in body

    def test_omits_dashboard_link_when_host_unset(self) -> None:
        body = _build_escalation_message_body(
            row=_row(summary="Summary text"), host_base_url=""
        )
        assert "/admin/escalations" not in body


# ---------------------------------------------------------------------
# _build_attachment
# ---------------------------------------------------------------------


class TestAttachment:
    def test_returns_none_when_flag_off(self, tmp_path: Path) -> None:
        cfg = PromptDeliveryConfig(attach_full_prompt_to_discord=False)
        path = tmp_path / "cid-1.md"
        path.write_text("hello")
        att = _build_attachment(
            row=_row(prompt_path=str(path)), prompt_delivery=cfg
        )
        assert att is None

    def test_returns_none_when_path_unset(self) -> None:
        cfg = PromptDeliveryConfig()
        att = _build_attachment(row=_row(prompt_path=None), prompt_delivery=cfg)
        assert att is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        cfg = PromptDeliveryConfig()
        att = _build_attachment(
            row=_row(prompt_path=str(tmp_path / "missing.md")),
            prompt_delivery=cfg,
        )
        assert att is None

    def test_returns_none_when_oversize(self, tmp_path: Path) -> None:
        cfg = PromptDeliveryConfig(attachment_size_limit_mb=0)
        path = tmp_path / "cid-1.md"
        path.write_text("hello world")
        att = _build_attachment(
            row=_row(prompt_path=str(path)), prompt_delivery=cfg
        )
        assert att is None

    def test_builds_discord_file_for_real_path(self, tmp_path: Path) -> None:
        cfg = PromptDeliveryConfig()
        path = tmp_path / "cid-1.md"
        path.write_text("# Hello\n")
        att = _build_attachment(
            row=_row(prompt_path=str(path)), prompt_delivery=cfg
        )
        # discord.File proxies the underlying file handle; we just want
        # to make sure something non-None came back so the deliver path
        # tries to upload it.
        assert att is not None


# ---------------------------------------------------------------------
# Full delivery callback
# ---------------------------------------------------------------------


@pytest.fixture
def mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message_with_view = AsyncMock(return_value=MagicMock())
    return bot


class TestDeliveryCallback:
    async def test_calls_bot_with_attachment_when_chat_mode(
        self, tmp_path: Path, mock_bot: MagicMock
    ) -> None:
        path = tmp_path / "cid-1.md"
        path.write_text("# Prompt body\n")
        cfg = PromptDeliveryConfig()
        gate = MagicMock()
        deliver = _make_escalation_delivery_callback(
            bot=mock_bot,
            owner_discord_id=4242,
            gate_holder=lambda: gate,
            prompt_delivery=cfg,
        )
        ok = await deliver(_row(prompt_path=str(path)))
        assert ok is True
        kwargs = mock_bot.send_message_with_view.call_args.kwargs
        assert "file" in kwargs and kwargs["file"] is not None

    async def test_attachment_upload_failure_falls_back_to_no_attachment(
        self, tmp_path: Path, mock_bot: MagicMock
    ) -> None:
        path = tmp_path / "cid-1.md"
        path.write_text("# Body")
        cfg = PromptDeliveryConfig()
        gate = MagicMock()

        # First call (with attachment) raises; second call (no attachment) succeeds.
        attempts: list[dict] = []

        async def _send(*args, **kwargs):
            attempts.append(dict(kwargs))
            if "file" in kwargs and kwargs["file"] is not None:
                raise RuntimeError("rate limited")
            return MagicMock()

        mock_bot.send_message_with_view = AsyncMock(side_effect=_send)

        deliver = _make_escalation_delivery_callback(
            bot=mock_bot,
            owner_discord_id=4242,
            gate_holder=lambda: gate,
            prompt_delivery=cfg,
        )
        ok = await deliver(_row(prompt_path=str(path)))
        assert ok is True
        # Two attempts: first with file, second without.
        assert len(attempts) == 2
        assert attempts[0].get("file") is not None
        assert attempts[1].get("file") is None
