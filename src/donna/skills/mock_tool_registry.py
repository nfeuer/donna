"""Deny-closed tool registry for validation runs.

See spec §6.2. Used by ValidationExecutor so fixture validation never
dispatches a real tool callable. A real callable can never be registered
on a MockToolRegistry — :meth:`register` raises.
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

from donna.skills.tool_fingerprint import fingerprint
from donna.skills.tool_registry import (
    ToolNotAllowedError,
    ToolRegistry,
)

logger = structlog.get_logger()


class UnmockedToolError(Exception):
    """Raised when a skill step tries to dispatch a tool whose invocation
    has no matching mock in the fixture's ``tool_mocks`` blob.
    """

    def __init__(self, tool_name: str, fingerprint_str: str) -> None:
        super().__init__(
            f"no mock for tool {tool_name!r} with fingerprint {fingerprint_str!r}"
        )
        self.tool_name = tool_name
        self.fingerprint = fingerprint_str


class MockToolRegistry(ToolRegistry):
    """ToolRegistry that dispatches from a precomputed mock map."""

    _ERROR_WHITELIST: ClassVar[dict[str, type[Exception]]] = {
        "TimeoutError": TimeoutError,
        "ConnectionError": ConnectionError,
        "ValueError": ValueError,
        "RuntimeError": RuntimeError,
        "OSError": OSError,
    }

    def __init__(self, mocks: dict[str, Any]) -> None:
        super().__init__()
        self._mocks = dict(mocks)

    @classmethod
    def from_mocks(cls, mocks: dict[str, Any] | None) -> MockToolRegistry:
        return cls(mocks or {})

    def register(self, name: str, callable_: Any) -> None:
        raise RuntimeError(
            "MockToolRegistry does not accept real tool callables; "
            "construct with the tool_mocks map instead."
        )

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        if tool_name not in allowed_tools:
            raise ToolNotAllowedError(
                f"tool {tool_name!r} not in step allowlist {allowed_tools}"
            )
        fp = fingerprint(tool_name, args)
        if fp not in self._mocks:
            logger.warning(
                "unmocked_tool_call",
                tool_name=tool_name, fingerprint=fp,
            )
            raise UnmockedToolError(tool_name, fp)

        mock = self._mocks[fp]
        if isinstance(mock, dict) and "__error__" in mock:
            exc_class_name = mock["__error__"]
            message = mock.get("__message__", "")
            exc_class = self._ERROR_WHITELIST.get(exc_class_name)
            if exc_class is None:
                logger.warning("unknown_error_class_in_mock", requested_class=exc_class_name)
                exc_class = RuntimeError
            raise exc_class(message)
        return mock
