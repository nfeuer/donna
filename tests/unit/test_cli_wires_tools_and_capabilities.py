"""Regression tests confirming wire_skill_system threads gmail_client through to
register_default_tools.

Wave 4 Task 8: GmailClient must flow from wire_skill_system caller into
register_default_tools so Gmail skill tools are registered at boot.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from donna.cli_wiring import StartupContext, wire_skill_system
from donna.config import SkillSystemConfig


def _make_ctx(skill_enabled: bool = False) -> StartupContext:
    """Build a minimal StartupContext suitable for unit-testing wire_skill_system.

    Sets skill_config.enabled=False by default so the function skips
    DB-dependent capability loading and bundle assembly — keeping the test
    fast and isolated.
    """
    return StartupContext(
        args=argparse.Namespace(config_dir="config", log_level="INFO", dev=True, port=8100),
        config_dir=Path("config"),
        project_root=Path("."),
        log=MagicMock(),
        models_config=MagicMock(),
        task_types_config=MagicMock(),
        skill_config=SkillSystemConfig(enabled=skill_enabled),
        db=MagicMock(),
        state_machine=MagicMock(),
        router=MagicMock(),
        invocation_logger=MagicMock(),
        input_parser=MagicMock(),
        port=8100,
        user_id="nick",
        discord_token=None,
        tasks_channel_id_str=None,
        debug_channel_id_str=None,
        agents_channel_id_str=None,
        guild_id_str=None,
        bot=None,
        notification_service=None,
    )


@pytest.mark.asyncio
async def test_wire_skill_system_passes_gmail_client_to_register_default_tools():
    """gmail_client kwarg must be forwarded from wire_skill_system to register_default_tools."""
    ctx = _make_ctx(skill_enabled=False)
    fake_gmail = MagicMock(name="GmailClient")

    # Patch at the import site inside cli_wiring (it imports tools as a module,
    # then calls _skill_tools_module.register_default_tools).
    with patch("donna.skills.tools.register_default_tools") as mock_reg, \
         patch("donna.models.router.ModelRouter") as _mock_router:
        # ModelRouter is constructed inside wire_skill_system; stub it out.
        _mock_router.return_value = MagicMock()
        # Also patch the module-level registry attribute to avoid side-effects.
        with patch("donna.skills.tools.DEFAULT_TOOL_REGISTRY", MagicMock()):
            await wire_skill_system(ctx, gmail_client=fake_gmail)

    mock_reg.assert_called_once()
    _call_args, call_kwargs = mock_reg.call_args
    assert call_kwargs.get("gmail_client") is fake_gmail, (
        "gmail_client was not forwarded to register_default_tools"
    )


@pytest.mark.asyncio
async def test_wire_skill_system_defaults_gmail_client_to_none():
    """Calling wire_skill_system without gmail_client passes None (backward compat)."""
    ctx = _make_ctx(skill_enabled=False)

    with patch("donna.skills.tools.register_default_tools") as mock_reg, \
         patch("donna.models.router.ModelRouter") as _mock_router:
        _mock_router.return_value = MagicMock()
        with patch("donna.skills.tools.DEFAULT_TOOL_REGISTRY", MagicMock()):
            await wire_skill_system(ctx)

    mock_reg.assert_called_once()
    _call_args, call_kwargs = mock_reg.call_args
    assert call_kwargs.get("gmail_client") is None
