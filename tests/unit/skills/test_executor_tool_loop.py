"""Unit tests for SkillExecutor._complete_with_tool_loop.

Tests the tool_use loop without real LLM calls — the router and tool
registry are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from donna.models.types import CompletionMetadata
from donna.skills.executor import SkillExecutor
from donna.skills.tool_registry import ToolRegistry


def _meta(cost: float = 0.01) -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=100, tokens_in=50, tokens_out=50,
        cost_usd=cost, model_actual="test/model",
    )


def _tool_use_output(
    tool_name: str = "web_fetch",
    tool_id: str = "call_1",
    tool_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "_tool_use": [
            {"id": tool_id, "name": tool_name, "input": tool_input or {"url": "https://example.com"}},
        ],
        "_content": [{"type": "tool_use", "id": tool_id, "name": tool_name}],
    }


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("web_fetch", AsyncMock(return_value={"status": 200, "body": "ok"}))
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> SkillExecutor:
    router = AsyncMock()
    return SkillExecutor(model_router=router, tool_registry=registry)


@pytest.mark.asyncio
async def test_no_tools_passthrough(executor: SkillExecutor) -> None:
    """When tool_definitions is None, the router is called directly."""
    executor._router.complete = AsyncMock(return_value=({"answer": 42}, _meta()))
    output, _result_meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=[], tool_definitions=None,
    )
    assert output == {"answer": 42}
    assert cost == pytest.approx(0.01)
    executor._router.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_round_tool_use(executor: SkillExecutor) -> None:
    """Router returns tool_use, tool dispatches, router returns text."""
    executor._router.complete = AsyncMock(side_effect=[
        (_tool_use_output(), _meta(0.02)),
        ({"answer": "fetched"}, _meta(0.03)),
    ])
    tools = [{"name": "web_fetch", "input_schema": {}}]
    output, _result_meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=["web_fetch"], tool_definitions=tools,
    )
    assert output == {"answer": "fetched"}
    assert cost == pytest.approx(0.05)
    assert executor._router.complete.await_count == 2


@pytest.mark.asyncio
async def test_multi_round_tool_use(executor: SkillExecutor) -> None:
    """Two rounds of tool calls before final text response."""
    executor._router.complete = AsyncMock(side_effect=[
        (_tool_use_output(tool_id="call_1"), _meta(0.01)),
        (_tool_use_output(tool_id="call_2"), _meta(0.01)),
        ({"answer": "done"}, _meta(0.01)),
    ])
    tools = [{"name": "web_fetch", "input_schema": {}}]
    output, _result_meta, cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=["web_fetch"], tool_definitions=tools,
    )
    assert output == {"answer": "done"}
    assert cost == pytest.approx(0.03)
    assert executor._router.complete.await_count == 3


@pytest.mark.asyncio
async def test_max_rounds_exceeded(executor: SkillExecutor) -> None:
    """RuntimeError raised after max_rounds of tool_use."""
    executor._router.complete = AsyncMock(
        return_value=(_tool_use_output(), _meta()),
    )
    tools = [{"name": "web_fetch", "input_schema": {}}]
    with pytest.raises(RuntimeError, match="exceeded 3 rounds"):
        await executor._complete_with_tool_loop(
            prompt="test", task_type="t", user_id="u",
            tool_names=["web_fetch"], tool_definitions=tools,
            max_rounds=3,
        )


@pytest.mark.asyncio
async def test_tool_dispatch_error(executor: SkillExecutor) -> None:
    """Tool dispatch failure sends is_error tool_result, loop continues."""
    executor._tool_registry = ToolRegistry()
    executor._tool_registry.register(
        "web_fetch", AsyncMock(side_effect=RuntimeError("connection refused")),
    )
    executor._router.complete = AsyncMock(side_effect=[
        (_tool_use_output(), _meta()),
        ({"answer": "recovered"}, _meta()),
    ])
    tools = [{"name": "web_fetch", "input_schema": {}}]
    output, _result_meta, _cost = await executor._complete_with_tool_loop(
        prompt="test", task_type="t", user_id="u",
        tool_names=["web_fetch"], tool_definitions=tools,
    )
    assert output == {"answer": "recovered"}
    # Verify the error tool_result was sent back in the messages
    second_call_kwargs = executor._router.complete.call_args_list[1].kwargs
    messages = second_call_kwargs["messages"]
    error_msg = messages[-1]
    assert error_msg["content"][0]["is_error"] is True
    assert "connection refused" in error_msg["content"][0]["content"]
