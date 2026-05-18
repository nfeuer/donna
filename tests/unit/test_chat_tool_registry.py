"""Tests for the chat tool registry (donna.chat.tools)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from donna.chat.tools import ToolRegistry


@pytest.fixture
def tools_yaml(tmp_path: Path) -> Path:
    config = {
        "tools": {
            "query_tasks": {
                "description": "Search tasks by status",
                "domain": "tasks",
                "type": "read",
                "handler": "donna.chat.tools.tasks.query_tasks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "default": 25, "maximum": 100},
                    },
                    "required": [],
                },
            },
            "create_task": {
                "description": "Create a new task",
                "domain": "tasks",
                "type": "write",
                "handler": "donna.chat.actions.tasks.create_task",
                "parameters": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            },
        }
    }
    path = tmp_path / "chat_tools.yaml"
    path.write_text(yaml.dump(config))
    return path


class TestToolRegistry:
    def test_loads_tools_from_yaml(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        assert len(registry.list_tools()) == 2

    def test_get_tool_by_name(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        tool = registry.get("query_tasks")
        assert tool is not None
        assert tool.domain == "tasks"
        assert tool.tool_type == "read"

    def test_get_unknown_tool_returns_none(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        assert registry.get("nonexistent") is None

    def test_is_read_tool(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        assert registry.is_read_tool("query_tasks") is True
        assert registry.is_read_tool("create_task") is False

    def test_schemas_for_prompt(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        schemas = registry.schemas_for_prompt()
        assert "query_tasks" in schemas
        assert "create_task" in schemas
        assert "Search tasks by status" in schemas

    def test_validate_params_valid(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        errors = registry.validate_params("query_tasks", {"status": "active"})
        assert errors is None

    def test_validate_params_missing_required(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        errors = registry.validate_params("create_task", {})
        assert errors is not None
        assert "title" in errors

    def test_validate_params_unknown_tool(self, tools_yaml: Path) -> None:
        registry = ToolRegistry.from_yaml(tools_yaml)
        errors = registry.validate_params("nonexistent", {})
        assert errors is not None
        assert "Unknown tool" in errors

    def test_missing_yaml_returns_empty_registry(self, tmp_path: Path) -> None:
        registry = ToolRegistry.from_yaml(tmp_path / "missing.yaml")
        assert len(registry.list_tools()) == 0
