"""Unit tests: MockToolRegistry recognizes __error__ shape and raises."""
from __future__ import annotations

import pytest

from donna.skills.mock_tool_registry import MockToolRegistry
from donna.skills.tool_fingerprint import fingerprint


@pytest.mark.asyncio
async def test_error_shape_raises_named_exception() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"__error__": "ConnectionError", "__message__": "boom"},
    })
    with pytest.raises(ConnectionError) as exc_info:
        await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])
    assert "boom" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unknown_exception_class_falls_back_to_runtime_error() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"__error__": "NonexistentError", "__message__": "x"},
    })
    with pytest.raises(RuntimeError):
        await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])


@pytest.mark.asyncio
async def test_normal_dict_still_returned() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"ok": True, "value": 42},
    })
    result = await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])
    assert result == {"ok": True, "value": 42}


@pytest.mark.asyncio
async def test_timeout_error_whitelist() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"__error__": "TimeoutError", "__message__": "slow"},
    })
    with pytest.raises(TimeoutError):
        await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])
