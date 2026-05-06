"""Unit tests for RuntimeToolCheck (slice 22)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.capabilities.runtime_tool_check import RuntimeToolCheck


class _FakeRegistry:
    def __init__(self, *names: str) -> None:
        self._names = list(names)

    def list_tool_names(self) -> list[str]:
        return list(self._names)


@pytest.mark.asyncio
async def test_returns_missing_tools_when_some_unregistered():
    registry = _FakeRegistry("calendar_read", "task_db_read")
    lookup = AsyncMock()
    lookup.list_required_tools = AsyncMock(
        return_value=["calendar_read", "web_fetch", "slack_post"]
    )
    check = RuntimeToolCheck(registry=registry, lookup=lookup)
    missing = await check.check("news_check")
    assert sorted(missing) == ["slack_post", "web_fetch"]


@pytest.mark.asyncio
async def test_returns_empty_when_all_registered():
    registry = _FakeRegistry("calendar_read", "task_db_read")
    lookup = AsyncMock()
    lookup.list_required_tools = AsyncMock(
        return_value=["calendar_read", "task_db_read"]
    )
    check = RuntimeToolCheck(registry=registry, lookup=lookup)
    assert await check.check("any_capability") == []


@pytest.mark.asyncio
async def test_returns_empty_when_capability_has_no_requirements():
    registry = _FakeRegistry()
    lookup = AsyncMock()
    lookup.list_required_tools = AsyncMock(return_value=[])
    check = RuntimeToolCheck(registry=registry, lookup=lookup)
    assert await check.check("anything") == []


@pytest.mark.asyncio
async def test_returns_empty_on_lookup_failure():
    registry = _FakeRegistry()
    lookup = AsyncMock()
    lookup.list_required_tools = AsyncMock(side_effect=RuntimeError("db down"))
    check = RuntimeToolCheck(registry=registry, lookup=lookup)
    assert await check.check("anything") == []
