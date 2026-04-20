"""Task 16: orchestrator registers default tools and seeds capabilities."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_orchestrator_registers_default_tools(monkeypatch, tmp_path) -> None:
    """register_default_tools is called during _run_orchestrator startup."""
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")

    called = {"n": 0}
    from donna.skills.tools import register_default_tools as orig

    def _capture(registry, **kwargs):
        called["n"] += 1
        orig(registry, **kwargs)

    monkeypatch.setattr("donna.skills.tools.register_default_tools", _capture)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir="config", log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)
    assert called["n"] >= 1


@pytest.mark.asyncio
async def test_orchestrator_seeds_capabilities_from_yaml(monkeypatch, tmp_path) -> None:
    """After boot, the capability table contains product_watch (from config/capabilities.yaml)."""
    # Use a clean DB so only the loader + migrations populate it.
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DONNA_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")

    # Copy project config to tmp so the loader has a real YAML to read.
    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    # Enable skill system so the loader runs.
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text()
    if "enabled: false" in content:
        skills_yaml.write_text(content.replace("enabled: false", "enabled: true", 1))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    # Verify the capability is present (either from the migration, the YAML loader, or both — idempotent).
    import aiosqlite
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1
