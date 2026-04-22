"""Integration: orchestrator constructs NotificationService."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from donna.notifications.service import NotificationService


@pytest.mark.asyncio
async def test_cli_constructs_notification_service(monkeypatch, tmp_path) -> None:
    """Run donna.cli._run_orchestrator in a mode that exits quickly, and verify
    NotificationService is constructed when Discord creds are present."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "12345")
    monkeypatch.setenv("DONNA_USER_ID", "nick")
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "test.db"))

    async def _noop(*args, **kwargs):
        return None

    constructed: list[NotificationService] = []
    original_init = NotificationService.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        constructed.append(self)

    monkeypatch.setattr(NotificationService, "__init__", _capturing_init)

    with (
        patch("donna.integrations.discord_bot.DonnaBot.start", _noop),
        patch("donna.server.run_server", _noop),
    ):
        from donna.cli import _run_orchestrator
        args = argparse.Namespace(
            config_dir="config", log_level="INFO", dev=True, port=8100,
        )
        await _run_orchestrator(args)

    assert len(constructed) == 1
