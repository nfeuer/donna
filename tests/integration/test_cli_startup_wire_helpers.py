"""cli_wiring helpers each return a handle and don't raise on happy-path config.

F-W2-E: `_run_orchestrator` has been refactored into a `StartupContext` +
three `wire_*` helpers. These tests exercise the helpers directly to
confirm each stage is independently callable, returns a handle, and runs
against the real project config without raising.

The original Wave-3 plan referenced a hypothetical `AppConfig` /
`load_config()` API. Donna's config layer is per-section (YAML loaders
returning pydantic models), so the fixture has been adapted to use the
real loaders + an argparse.Namespace matching `_run_orchestrator`'s
contract.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.cli_wiring import (
    StartupContext,
    build_startup_context,
    wire_automation_subsystem,
    wire_discord,
    wire_skill_system,
)


def _stub_calendar_client() -> MagicMock:
    """Return a MagicMock that satisfies CapabilityToolRegistryCheck."""
    c = MagicMock()
    c.list_events = AsyncMock(return_value=[])
    return c


def _args_for(tmp_path: pathlib.Path, config_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config_dir=str(config_dir),
        log_level="INFO",
        dev=True,
        port=8100,
    )


@pytest.fixture
def skill_enabled_config_dir(tmp_path: pathlib.Path) -> Path:
    """Copy project config into tmp and flip skill_system.enabled=true."""
    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text() if skills_yaml.exists() else ""
    if "enabled: false" in content:
        content = content.replace("enabled: false", "enabled: true", 1)
        skills_yaml.write_text(content)
    elif "enabled: true" not in content:
        skills_yaml.write_text("enabled: true\n" + content)
    return cfg_dst


@pytest.fixture
def minimal_env(monkeypatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))
    # Discord is optional for startup context; wire_discord tolerates no token.
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_TASKS_CHANNEL_ID", raising=False)
    yield


@pytest.mark.asyncio
async def test_build_startup_context_returns_context(
    minimal_env, skill_enabled_config_dir, tmp_path,
) -> None:
    args = _args_for(tmp_path, skill_enabled_config_dir)
    ctx = await build_startup_context(args)
    try:
        assert isinstance(ctx, StartupContext)
        assert ctx.db is not None
        assert ctx.router is not None
        assert ctx.skill_config is not None
    finally:
        await ctx.db.close()


@pytest.mark.asyncio
async def test_wire_skill_system_returns_handle(
    minimal_env, skill_enabled_config_dir, tmp_path,
) -> None:
    args = _args_for(tmp_path, skill_enabled_config_dir)
    ctx = await build_startup_context(args)
    try:
        handle = await wire_skill_system(
            ctx, calendar_client=_stub_calendar_client(),
        )
        assert handle is not None
        # On skill_system.enabled=true, the handle exposes the skill bundle
        # and cost tracker; on disabled it still exists but with bundle=None.
        assert hasattr(handle, "bundle")
        assert hasattr(handle, "subsystem_router")
    finally:
        await ctx.db.close()


@pytest.mark.asyncio
async def test_wire_automation_subsystem_returns_handle(
    minimal_env, skill_enabled_config_dir, tmp_path,
) -> None:
    args = _args_for(tmp_path, skill_enabled_config_dir)
    ctx = await build_startup_context(args)
    try:
        skill_h = await wire_skill_system(
            ctx, calendar_client=_stub_calendar_client(),
        )
        handle = await wire_automation_subsystem(ctx, skill_h)
        assert handle is not None
        assert handle.scheduler is not None
        assert handle.dispatcher is not None
        assert handle.repository is not None
    finally:
        await ctx.db.close()


@pytest.mark.asyncio
async def test_wire_automation_subsystem_registers_cadence_reclamper(
    minimal_env, skill_enabled_config_dir, tmp_path,
) -> None:
    """Bug-fix regression: CadenceReclamper must be registered on the skill
    lifecycle hook in production wiring. Harness used to be the only caller
    doing this, leaving cadence-uplift inert in prod.
    """
    args = _args_for(tmp_path, skill_enabled_config_dir)
    ctx = await build_startup_context(args)
    try:
        skill_h = await wire_skill_system(
            ctx, calendar_client=_stub_calendar_client(),
        )
        # If the skill bundle is disabled there's nothing to register against.
        assert skill_h.bundle is not None, (
            "skill_system must be enabled for this regression — check fixture"
        )
        _ = await wire_automation_subsystem(ctx, skill_h)
        subscribers = skill_h.bundle.lifecycle_manager.after_state_change._subscribers
        names = [getattr(fn, "__qualname__", repr(fn)) for fn in subscribers]
        assert any("reclamp_for_capability" in n for n in names), (
            f"CadenceReclamper.reclamp_for_capability not registered; "
            f"got subscribers: {names}"
        )
    finally:
        await ctx.db.close()


@pytest.mark.asyncio
async def test_wire_discord_returns_handle(
    minimal_env, skill_enabled_config_dir, tmp_path,
) -> None:
    args = _args_for(tmp_path, skill_enabled_config_dir)
    ctx = await build_startup_context(args)
    try:
        skill_h = await wire_skill_system(
            ctx, calendar_client=_stub_calendar_client(),
        )
        automation_h = await wire_automation_subsystem(ctx, skill_h)
        handle = await wire_discord(ctx, skill_h, automation_h)
        assert handle is not None
        # bot may be None if Discord token absent; wire function still runs.
        assert hasattr(handle, "bot")
        assert hasattr(handle, "intent_dispatcher")
        # F-W3-I: notification_service is intentionally NOT on the handle;
        # consumers should reach through StartupContext.notification_service.
        assert not hasattr(handle, "notification_service")
    finally:
        await ctx.db.close()
