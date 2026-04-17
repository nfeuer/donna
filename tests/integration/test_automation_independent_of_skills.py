"""F-W1-H: automation subsystem must wire whether or not skill_system is enabled."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_automation_dispatcher_wires_when_skill_system_disabled(
    monkeypatch, tmp_path,
) -> None:
    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text()
    # Flip enabled to false. Handle both true and false starting states.
    if "enabled: true" in content:
        content = content.replace("enabled: true", "enabled: false", 1)
    elif "enabled: false" not in content:
        content = "enabled: false\n" + content
    skills_yaml.write_text(content)

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))

    captured = []
    from donna.automations import dispatcher as dispatcher_module
    original_init = dispatcher_module.AutomationDispatcher.__init__

    def _capture(self, *a, **kw):
        original_init(self, *a, **kw)
        captured.append(self)

    monkeypatch.setattr(
        dispatcher_module.AutomationDispatcher, "__init__", _capture,
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
        f"AutomationDispatcher should wire even when skill_system.enabled=false; "
        f"got {len(captured)} instances"
    )
