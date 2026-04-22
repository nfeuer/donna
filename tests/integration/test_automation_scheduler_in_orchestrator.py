"""Integration: orchestrator constructs AutomationDispatcher with live notifier."""

from __future__ import annotations

import argparse

import pytest


@pytest.mark.asyncio
async def test_automation_dispatcher_uses_real_notification_service(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))

    # Enable skill system so the wiring block runs.
    import shutil
    from pathlib import Path
    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text() if skills_yaml.exists() else ""
    # Flip enabled: false -> enabled: true. If the file is missing or doesn't
    # contain enabled: false, prepend enabled: true (last-value-wins in YAML).
    if "enabled: false" in content:
        content = content.replace("enabled: false", "enabled: true", 1)
        skills_yaml.write_text(content)
    elif "enabled: true" not in content:
        skills_yaml.write_text("enabled: true\n" + content)

    captured = []
    from donna.automations import dispatcher as dispatcher_module
    original_init = dispatcher_module.AutomationDispatcher.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured.append(self)

    monkeypatch.setattr(
        dispatcher_module.AutomationDispatcher, "__init__", _capturing_init,
    )

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    assert len(captured) == 1, (
        f"Expected exactly one AutomationDispatcher instance, got {len(captured)}"
    )
    from donna.notifications.service import NotificationService
    assert isinstance(captured[0]._notifier, NotificationService)
