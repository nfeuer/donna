"""Tests: AutomationCreationPath rejects approval when required tool is missing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.creation_flow import (
    AutomationCreationPath,
    MissingToolError,
)
from donna.skills.tool_registry import ToolRegistry
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


def _make_draft(**overrides):
    base = dict(
        user_id="u1",
        capability_name="email_triage",
        inputs={"senders": ["jane@x.com"]},
        schedule_cron="0 */12 * * *",
        schedule_human="every 12 hours",
        target_cadence_cron="0 */12 * * *",
        active_cadence_cron="0 */12 * * *",
        alert_conditions={},
    )
    base.update(overrides)
    return DraftAutomation(**base)


@pytest.mark.asyncio
async def test_approve_rejects_when_required_tool_unregistered():
    reg = ToolRegistry()
    # Only register web_fetch + rss_fetch — gmail_search absent.
    reg.register("web_fetch", AsyncMock())
    reg.register("rss_fetch", AsyncMock())

    required_lookup = AsyncMock(return_value=["gmail_search"])
    repo = AsyncMock()
    path = AutomationCreationPath(
        repository=repo,
        tool_registry=reg,
        capability_tool_lookup=required_lookup,
    )

    with pytest.raises(MissingToolError) as ei:
        await path.approve(_make_draft(), name="triage-jane")
    assert "gmail_search" in str(ei.value)
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_approve_proceeds_when_tools_registered():
    reg = ToolRegistry()
    reg.register("gmail_search", AsyncMock())

    required_lookup = AsyncMock(return_value=["gmail_search"])
    repo = AsyncMock()
    repo.create = AsyncMock(return_value="auto1")

    path = AutomationCreationPath(
        repository=repo,
        tool_registry=reg,
        capability_tool_lookup=required_lookup,
    )

    out = await path.approve(_make_draft(), name="triage-jane")
    assert out == "auto1"
    repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_approve_backward_compat_without_guard_deps():
    """When tool_registry/lookup aren't wired, approve() behaves as before."""
    repo = AsyncMock()
    repo.create = AsyncMock(return_value="auto2")
    path = AutomationCreationPath(repository=repo)
    out = await path.approve(_make_draft(), name="triage-jane")
    assert out == "auto2"
