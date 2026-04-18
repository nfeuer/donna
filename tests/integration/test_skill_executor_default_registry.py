"""F-W2-C: SkillExecutor without explicit tool_registry uses DEFAULT_TOOL_REGISTRY."""
from __future__ import annotations

from unittest.mock import MagicMock

from donna.skills.executor import SkillExecutor
from donna.skills.tools import DEFAULT_TOOL_REGISTRY


def test_executor_uses_default_tool_registry_when_not_overridden() -> None:
    fake_router = MagicMock()
    executor = SkillExecutor(model_router=fake_router)
    assert executor._tool_registry is DEFAULT_TOOL_REGISTRY


def test_executor_allows_explicit_registry_override() -> None:
    fake_router = MagicMock()
    from donna.skills.tool_registry import ToolRegistry
    custom = ToolRegistry()
    executor = SkillExecutor(model_router=fake_router, tool_registry=custom)
    assert executor._tool_registry is custom
