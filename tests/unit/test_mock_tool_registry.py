"""Tests for donna.skills.mock_tool_registry."""

from __future__ import annotations

import pytest
from donna.skills.mock_tool_registry import MockToolRegistry, UnmockedToolError


@pytest.mark.asyncio
async def test_empty_mocks_always_raises() -> None:
    reg = MockToolRegistry({})
    with pytest.raises(UnmockedToolError):
        await reg.dispatch("web_fetch", {"url": "https://x"}, allowed_tools=["web_fetch"])


@pytest.mark.asyncio
async def test_dispatches_from_mocks() -> None:
    mocks = {
        'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"},
    }
    reg = MockToolRegistry(mocks)
    result = await reg.dispatch(
        "web_fetch",
        {"url": "https://x", "timeout_s": 10},
        allowed_tools=["web_fetch"],
    )
    assert result == {"status": 200, "body": "OK"}


@pytest.mark.asyncio
async def test_unmocked_raises_with_fingerprint_in_message() -> None:
    reg = MockToolRegistry({})
    with pytest.raises(UnmockedToolError) as excinfo:
        await reg.dispatch("web_fetch", {"url": "https://y"}, allowed_tools=["web_fetch"])
    assert "web_fetch" in str(excinfo.value)
    assert "https://y" in str(excinfo.value)


@pytest.mark.asyncio
async def test_from_mocks_classmethod_handles_none() -> None:
    reg = MockToolRegistry.from_mocks(None)
    with pytest.raises(UnmockedToolError):
        await reg.dispatch("web_fetch", {"url": "https://z"}, allowed_tools=["web_fetch"])


@pytest.mark.asyncio
async def test_respects_allowed_tools() -> None:
    mocks = {'web_fetch:{"url":"https://x"}': {"status": 200}}
    reg = MockToolRegistry(mocks)
    from donna.skills.tool_registry import ToolNotAllowedError
    with pytest.raises(ToolNotAllowedError):
        await reg.dispatch(
            "web_fetch", {"url": "https://x"},
            allowed_tools=["gmail_read"],
        )


def test_register_raises_runtime_error() -> None:
    reg = MockToolRegistry({})
    with pytest.raises(RuntimeError):
        reg.register("web_fetch", lambda **_: None)
