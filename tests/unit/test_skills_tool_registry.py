import pytest

from donna.skills.tool_registry import ToolRegistry, ToolNotAllowedError, ToolNotFoundError


async def _mock_tool(**kwargs):
    return {"echo": kwargs}


async def test_register_and_dispatch():
    registry = ToolRegistry()
    registry.register("mock_tool", _mock_tool)
    result = await registry.dispatch(
        tool_name="mock_tool",
        args={"x": 1},
        allowed_tools=["mock_tool"],
    )
    assert result == {"echo": {"x": 1}}


async def test_dispatch_respects_allowlist():
    registry = ToolRegistry()
    registry.register("mock_tool", _mock_tool)
    with pytest.raises(ToolNotAllowedError, match="not in step allowlist"):
        await registry.dispatch(
            tool_name="mock_tool",
            args={},
            allowed_tools=["other_tool"],
        )


async def test_dispatch_raises_on_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await registry.dispatch(tool_name="missing", args={}, allowed_tools=["missing"])


async def test_register_overwrites_existing():
    registry = ToolRegistry()
    registry.register("tool", _mock_tool)

    async def other(**kwargs):
        return {"v": 2}

    registry.register("tool", other)
    result = await registry.dispatch("tool", {}, allowed_tools=["tool"])
    assert result == {"v": 2}


async def test_list_tool_names():
    registry = ToolRegistry()
    registry.register("a", _mock_tool)
    registry.register("b", _mock_tool)
    assert sorted(registry.list_tool_names()) == ["a", "b"]
