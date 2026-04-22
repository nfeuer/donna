"""Central registry of skill-layer tools.

Phase 2: tools are registered at app startup via register(). Skills
declare per-step allowlists in YAML; the executor calls dispatch() with
the allowlist, which enforces that the tool is permitted on the step.

Tools are async callables accepting keyword arguments and returning a
JSON-serializable dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger()


ToolCallable = Callable[..., Awaitable[dict]]


class ToolNotFoundError(Exception):
    """Raised when a skill asks for a tool that isn't registered."""


class ToolNotAllowedError(Exception):
    """Raised when a skill step tries to dispatch a tool not in its allowlist."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolCallable] = {}

    def register(self, name: str, callable_: ToolCallable) -> None:
        if name in self._tools:
            logger.info("tool_overwritten", name=name)
        self._tools[name] = callable_

    def clear(self) -> None:
        """Remove every registered tool.

        Intended for test teardown (via the autouse fixture in
        tests/conftest.py) and boot-time reinitialization. Not thread-safe;
        do not call from request-serving code paths.
        """
        self._tools.clear()

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    async def dispatch(
        self,
        tool_name: str,
        args: dict,
        allowed_tools: list[str],
    ) -> dict:
        if tool_name not in allowed_tools:
            raise ToolNotAllowedError(
                f"tool {tool_name!r} not in step allowlist {allowed_tools}"
            )
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"tool {tool_name!r} not registered")

        tool = self._tools[tool_name]
        return await tool(**args)
