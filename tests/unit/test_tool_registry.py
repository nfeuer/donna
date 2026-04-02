"""Tests for the agent tool registry."""

from __future__ import annotations

import pytest

from donna.agents.tool_registry import (
    ToolNotAllowedError,
    ToolNotRegisteredError,
    ToolRegistry,
)
from donna.config import TaskTypeEntry, TaskTypesConfig


@pytest.fixture
def task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "parse_task": TaskTypeEntry(
                description="Parse task",
                model="parser",
                prompt_template="prompts/parse_task.md",
                output_schema="schemas/task_parse_output.json",
                tools=["task_db_read", "calendar_read"],
            ),
            "generate_digest": TaskTypeEntry(
                description="Generate digest",
                model="parser",
                prompt_template="prompts/morning_digest.md",
                output_schema="schemas/digest_output.json",
                tools=["calendar_read", "task_db_read", "cost_summary"],
            ),
            "no_tools": TaskTypeEntry(
                description="No tools",
                model="parser",
                prompt_template="prompts/test.md",
                output_schema="schemas/test.json",
                tools=[],
            ),
        }
    )


@pytest.fixture
def registry(task_types_config: TaskTypesConfig) -> ToolRegistry:
    return ToolRegistry(task_types_config)


class TestIsAllowed:
    def test_allowed_tool(self, registry: ToolRegistry) -> None:
        assert registry.is_allowed("parse_task", "task_db_read") is True
        assert registry.is_allowed("parse_task", "calendar_read") is True

    def test_disallowed_tool(self, registry: ToolRegistry) -> None:
        assert registry.is_allowed("parse_task", "task_db_write") is False

    def test_unknown_task_type(self, registry: ToolRegistry) -> None:
        assert registry.is_allowed("nonexistent", "task_db_read") is False

    def test_no_tools_task(self, registry: ToolRegistry) -> None:
        assert registry.is_allowed("no_tools", "task_db_read") is False


class TestGetAllowedTools:
    def test_returns_tools_list(self, registry: ToolRegistry) -> None:
        tools = registry.get_allowed_tools("parse_task")
        assert "task_db_read" in tools
        assert "calendar_read" in tools
        assert len(tools) == 2

    def test_unknown_task_type(self, registry: ToolRegistry) -> None:
        assert registry.get_allowed_tools("nonexistent") == []


class TestExecute:
    async def test_execute_registered_tool(self, registry: ToolRegistry) -> None:
        async def mock_handler(**kwargs: object) -> dict:
            return {"events": [{"title": "Meeting"}]}

        registry.register("calendar_read", mock_handler)
        result = await registry.execute("calendar_read", {"lookahead_days": 7})
        assert result["events"][0]["title"] == "Meeting"

    async def test_execute_with_task_type_check(self, registry: ToolRegistry) -> None:
        async def mock_handler(**kwargs: object) -> dict:
            return {"count": 5}

        registry.register("task_db_read", mock_handler)
        result = await registry.execute(
            "task_db_read", {}, task_type="parse_task"
        )
        assert result["count"] == 5

    async def test_disallowed_tool_raises(self, registry: ToolRegistry) -> None:
        async def mock_handler(**kwargs: object) -> dict:
            return {}

        registry.register("task_db_write", mock_handler)
        with pytest.raises(ToolNotAllowedError, match="not allowed"):
            await registry.execute(
                "task_db_write", {}, task_type="parse_task"
            )

    async def test_unregistered_tool_raises(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotRegisteredError, match="No handler"):
            await registry.execute("unknown_tool", {})
