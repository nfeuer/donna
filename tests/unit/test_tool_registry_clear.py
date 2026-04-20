"""Unit tests for ToolRegistry.clear()."""
from __future__ import annotations

import pytest

from donna.skills.tool_registry import ToolRegistry


@pytest.mark.asyncio
async def test_clear_removes_all_tools() -> None:
    registry = ToolRegistry()

    async def _noop(**_: object) -> dict:
        return {"ok": True}

    registry.register("t1", _noop)
    registry.register("t2", _noop)
    assert registry.list_tool_names() == ["t1", "t2"]

    registry.clear()
    assert registry.list_tool_names() == []


def test_clear_is_idempotent() -> None:
    registry = ToolRegistry()
    registry.clear()  # no-op
    registry.clear()  # still no-op
    assert registry.list_tool_names() == []
