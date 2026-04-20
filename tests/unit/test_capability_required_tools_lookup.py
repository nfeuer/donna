"""Tests: SkillToolRequirementsLookup.list_required_tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.capabilities.tool_requirements import SkillToolRequirementsLookup


def _make_conn(yaml_backbone: str | None) -> MagicMock:
    """Build a minimal aiosqlite-like connection mock."""
    conn = MagicMock()
    cursor = AsyncMock()
    if yaml_backbone is None:
        cursor.fetchone = AsyncMock(return_value=None)
    else:
        cursor.fetchone = AsyncMock(return_value=(yaml_backbone,))
    conn.execute = AsyncMock(return_value=cursor)
    return conn


@pytest.mark.asyncio
async def test_list_required_tools_returns_union_from_yaml():
    yaml_src = """
steps:
  - name: search
    tools: [gmail_search, web_fetch]
  - name: fetch
    tools: [gmail_get_message, web_fetch]
"""
    conn = _make_conn(yaml_src)
    lookup = SkillToolRequirementsLookup(conn)
    result = await lookup.list_required_tools("email_triage")
    # sorted unique union
    assert result == ["gmail_get_message", "gmail_search", "web_fetch"]


@pytest.mark.asyncio
async def test_list_required_tools_unknown_capability_returns_empty():
    conn = _make_conn(None)
    lookup = SkillToolRequirementsLookup(conn)
    result = await lookup.list_required_tools("nonexistent_capability")
    assert result == []


@pytest.mark.asyncio
async def test_list_required_tools_empty_steps_returns_empty():
    yaml_src = "steps: []"
    conn = _make_conn(yaml_src)
    lookup = SkillToolRequirementsLookup(conn)
    result = await lookup.list_required_tools("some_capability")
    assert result == []


@pytest.mark.asyncio
async def test_list_required_tools_db_exception_returns_empty():
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=Exception("DB error"))
    lookup = SkillToolRequirementsLookup(conn)
    result = await lookup.list_required_tools("broken_capability")
    assert result == []
