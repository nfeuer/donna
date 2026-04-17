import pytest

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tool_dispatch import (
    ToolDispatcher,
    ToolInvocationError,
    ToolInvocationSpec,
)


async def test_basic_dispatch_with_jinja_args():
    async def echo(**kwargs):
        return {"got": kwargs}

    registry = ToolRegistry()
    registry.register("echo", echo)
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="echo",
            args={"url": "{{ inputs.url }}", "size": "{{ state.plan.size }}"},
            store_as="result",
        ),
        state={"plan": {"size": "L"}},
        inputs={"url": "https://x.com"},
        allowed_tools=["echo"],
    )

    assert result == {"result": {"got": {"url": "https://x.com", "size": "L"}}}


async def test_dispatch_retries_on_failure():
    attempts = {"count": 0}

    async def flaky(**kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}

    registry = ToolRegistry()
    registry.register("flaky", flaky)
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="flaky",
            args={},
            retry={"max_attempts": 3, "backoff_s": [0, 0, 0]},
            store_as="r",
        ),
        state={},
        inputs={},
        allowed_tools=["flaky"],
    )

    assert result == {"r": {"ok": True}}
    assert attempts["count"] == 3


async def test_dispatch_raises_after_retry_exhausted():
    async def always_fail(**kwargs):
        raise RuntimeError("permanent")

    registry = ToolRegistry()
    registry.register("fail", always_fail)
    dispatcher = ToolDispatcher(registry)

    with pytest.raises(ToolInvocationError, match="permanent"):
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(
                tool="fail",
                args={},
                retry={"max_attempts": 2, "backoff_s": [0, 0]},
                store_as="r",
            ),
            state={}, inputs={}, allowed_tools=["fail"],
        )


async def test_dispatch_respects_allowlist():
    async def ok(**kwargs):
        return {"v": 1}

    registry = ToolRegistry()
    registry.register("tool1", ok)
    dispatcher = ToolDispatcher(registry)

    with pytest.raises(ToolInvocationError):
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(tool="tool1", args={}, store_as="r"),
            state={}, inputs={}, allowed_tools=["tool2"],
        )


async def test_dispatch_renders_nested_args():
    async def echo(**kwargs):
        return {"got": kwargs}

    registry = ToolRegistry()
    registry.register("echo", echo)
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="echo",
            args={"config": {"url": "{{ inputs.u }}", "timeout": 5}, "tags": ["{{ inputs.t }}", "static"]},
            store_as="r",
        ),
        state={}, inputs={"u": "https://a.com", "t": "tag1"},
        allowed_tools=["echo"],
    )

    assert result["r"]["got"]["config"]["url"] == "https://a.com"
    assert result["r"]["got"]["tags"] == ["tag1", "static"]
