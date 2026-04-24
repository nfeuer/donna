"""Tool registry + agent allowlist integration for `memory_search`."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from donna.skills.tools import DEFAULT_TOOL_REGISTRY, register_default_tools


class _StubStore:
    async def search(self, **_kwargs: object) -> list[object]:
        return []


@pytest.mark.integration
def test_memory_search_registers_when_store_present() -> None:
    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY, memory_store=_StubStore())
    assert "memory_search" in DEFAULT_TOOL_REGISTRY.list_tool_names()


@pytest.mark.integration
def test_memory_search_absent_when_store_missing() -> None:
    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY)
    assert "memory_search" not in DEFAULT_TOOL_REGISTRY.list_tool_names()


@pytest.mark.integration
def test_agent_allowlist_includes_memory_search() -> None:
    raw = yaml.safe_load(Path("config/agents.yaml").read_text())
    agents = raw["agents"]
    for name in ("pm", "scheduler", "research", "challenger"):
        tools = agents[name]["allowed_tools"]
        assert "memory_search" in tools, f"{name}.allowed_tools missing memory_search"


@pytest.mark.integration
async def test_memory_search_tool_dispatchable() -> None:
    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY, memory_store=_StubStore())
    out = await DEFAULT_TOOL_REGISTRY.dispatch(
        "memory_search",
        {"query": "q", "user_id": "nick"},
        ["memory_search"],
    )
    assert out == {"ok": True, "query": "q", "count": 0, "results": []}
