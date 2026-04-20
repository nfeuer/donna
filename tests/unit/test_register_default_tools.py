"""Tests for the extended register_default_tools signature in Wave 4."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tools import register_default_tools


def test_registers_web_fetch_and_rss_fetch_without_gmail_client():
    reg = ToolRegistry()
    register_default_tools(reg)
    names = set(reg.list_tool_names())
    assert "web_fetch" in names
    assert "rss_fetch" in names
    assert "gmail_search" not in names
    assert "gmail_get_message" not in names


def test_registers_gmail_tools_when_client_provided():
    reg = ToolRegistry()
    fake_client = MagicMock()
    register_default_tools(reg, gmail_client=fake_client)
    names = set(reg.list_tool_names())
    assert "gmail_search" in names
    assert "gmail_get_message" in names


@pytest.mark.asyncio
async def test_registered_gmail_search_binds_the_client():
    from unittest.mock import AsyncMock

    from datetime import datetime, timezone

    class _FakeMsg:
        id = "m1"; sender = "x@y"; subject = "s"; snippet = "sn"
        date = datetime(2026, 4, 20, tzinfo=timezone.utc)
        recipients = []

    fake = MagicMock()
    fake.search_emails = AsyncMock(return_value=[_FakeMsg()])
    reg = ToolRegistry()
    register_default_tools(reg, gmail_client=fake)

    out = await reg.dispatch(
        "gmail_search",
        {"query": "from:x@y"},
        allowed_tools=["gmail_search"],
    )
    assert out["ok"] is True
    assert len(out["messages"]) == 1
