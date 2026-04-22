"""Tool validation and execution layer for agent tool calls.

Models propose tool calls; the orchestrator validates against the
task_types.yaml allowlist and executes. Models never call tools directly.

See docs/agents.md — Tool Execution Architecture.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from donna.config import TaskTypesConfig

logger = structlog.get_logger()


class ToolNotAllowedError(Exception):
    """Raised when a model requests a tool not allowed for the task type."""


class ToolNotRegisteredError(Exception):
    """Raised when a tool call references an unknown tool."""


class ToolRegistry:
    """Validates and executes tool calls proposed by LLM agents.

    Tool access is enforced per task type via config/task_types.yaml.
    A task type with ``tools: [calendar_read]`` cannot result in a
    ``task_db_write`` call regardless of what the model requests.
    """

    def __init__(self, task_types_config: TaskTypesConfig) -> None:
        self._config = task_types_config
        self._handlers: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {}

    def register(
        self, name: str, handler: Callable[..., Awaitable[dict[str, Any]]]
    ) -> None:
        """Register a tool handler.

        Args:
            name: Tool name matching entries in task_types.yaml ``tools`` lists.
            handler: Async callable that accepts keyword args and returns a dict.
        """
        self._handlers[name] = handler
        logger.info("tool_registered", tool=name)

    def is_allowed(self, task_type: str, tool_name: str) -> bool:
        """Check if a tool is permitted for the given task type."""
        tt = self._config.task_types.get(task_type)
        if tt is None:
            return False
        return tool_name in tt.tools

    def get_allowed_tools(self, task_type: str) -> list[str]:
        """Return the list of tools allowed for a task type."""
        tt = self._config.task_types.get(task_type)
        if tt is None:
            return []
        return list(tt.tools)

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        task_type: str | None = None,
    ) -> dict[str, Any]:
        """Validate and execute a tool call.

        Args:
            tool_name: The tool to execute.
            params: Keyword arguments for the tool handler.
            task_type: If provided, enforces the task_type allowlist.

        Returns:
            Tool execution result as a dict.

        Raises:
            ToolNotAllowedError: If the tool is not in the task type's allowlist.
            ToolNotRegisteredError: If no handler is registered for the tool.
        """
        if task_type is not None and not self.is_allowed(task_type, tool_name):
            raise ToolNotAllowedError(
                f"Tool {tool_name!r} is not allowed for task type {task_type!r}. "
                f"Allowed: {self.get_allowed_tools(task_type)}"
            )

        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ToolNotRegisteredError(
                f"No handler registered for tool {tool_name!r}. "
                f"Registered: {list(self._handlers.keys())}"
            )

        logger.info("tool_executing", tool=tool_name, task_type=task_type)
        result = await handler(**params)
        logger.info("tool_executed", tool=tool_name, task_type=task_type)
        return result
