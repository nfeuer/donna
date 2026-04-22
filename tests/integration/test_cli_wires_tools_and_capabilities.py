"""Task 16: orchestrator registers default tools and seeds capabilities."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest


def _stub_calendar_client() -> MagicMock:
    """Return a MagicMock GoogleCalendarClient that satisfies tool registration.

    calendar_read is only registered when a calendar client is present; tests
    that enable skill_system need one so CapabilityToolRegistryCheck passes.
    """
    c = MagicMock()
    c.list_events = AsyncMock(return_value=[])
    return c


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
    monkeypatch.setattr(
        "donna.cli_wiring._try_build_calendar_client",
        lambda _cfg: _stub_calendar_client(),
    )

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
    monkeypatch.setattr(
        "donna.cli_wiring._try_build_calendar_client",
        lambda _cfg: _stub_calendar_client(),
    )
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    # Verify the capability is present (either from the migration, the YAML loader,
    # or both — idempotent).
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_orchestrator_registers_wave_one_tools(monkeypatch, tmp_path) -> None:
    """After boot, the Wave-1 tools are present in DEFAULT_TOOL_REGISTRY.

    ``task_db_read`` and ``cost_summary`` always register (dependencies are
    constructed unconditionally). ``calendar_read`` and ``email_read`` depend
    on external credentials and are absent in this test environment.
    """
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DONNA_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")

    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text()
    if "enabled: false" in content:
        skills_yaml.write_text(content.replace("enabled: false", "enabled: true", 1))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(
        "donna.cli_wiring._try_build_calendar_client",
        lambda _cfg: _stub_calendar_client(),
    )
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    from donna.skills.tools import DEFAULT_TOOL_REGISTRY
    names = set(DEFAULT_TOOL_REGISTRY.list_tool_names())
    assert "task_db_read" in names
    assert "cost_summary" in names


@pytest.mark.asyncio
async def test_capability_tool_registry_check_passes_on_seeded_yaml(
    monkeypatch, tmp_path,
) -> None:
    """Orchestrator boot runs the check against the real capabilities.yaml."""
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DONNA_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")

    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text()
    if "enabled: false" in content:
        skills_yaml.write_text(content.replace("enabled: false", "enabled: true", 1))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(
        "donna.cli_wiring._try_build_calendar_client",
        lambda _cfg: _stub_calendar_client(),
    )
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    # Should not raise — every tool referenced from capabilities.yaml is
    # registered after boot.
    await _run_orchestrator(args)

    # Double-check: the seeded Claude-native capabilities persisted their
    # tools_json as the loader ran.
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name, tools_json FROM capability "
            "WHERE name IN ('generate_digest', 'task_decompose', "
            "'prep_research', 'extract_preferences')"
        )
        rows = {name: tools for name, tools in await cursor.fetchall()}
    assert rows["generate_digest"] is not None
    assert "calendar_read" in rows["generate_digest"]
    assert rows["task_decompose"] == "[]"


@pytest.mark.asyncio
async def test_capability_tool_registry_check_fails_on_unregistered_tool(
    monkeypatch, tmp_path,
) -> None:
    """Boot fails loudly when a capability references a nonexistent tool."""
    import yaml

    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DONNA_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")

    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text()
    if "enabled: false" in content:
        skills_yaml.write_text(content.replace("enabled: false", "enabled: true", 1))

    # Inject a bogus tool reference.
    cap_yaml = cfg_dst / "capabilities.yaml"
    data = yaml.safe_load(cap_yaml.read_text())
    for entry in data["capabilities"]:
        if entry["name"] == "task_decompose":
            entry["tools"] = ["definitely_not_a_registered_tool"]
    cap_yaml.write_text(yaml.safe_dump(data))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(
        "donna.cli_wiring._try_build_calendar_client",
        lambda _cfg: _stub_calendar_client(),
    )
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.capabilities.capability_tool_check import CapabilityToolConfigError
    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    with pytest.raises(CapabilityToolConfigError):
        await _run_orchestrator(args)
