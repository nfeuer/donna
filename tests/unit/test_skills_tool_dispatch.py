import pytest

from donna.skills.tool_dispatch import (
    ToolDispatcher,
    ToolInvocationError,
    ToolInvocationSpec,
)
from donna.skills.tool_registry import ToolRegistry


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


async def test_param_validation_error_is_not_retried():
    """A deterministic ParameterValidationError must fail fast, not retry."""
    calls = {"n": 0}

    async def handler(**kwargs):
        calls["n"] += 1
        return {"ok": True}

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }
    registry = ToolRegistry()
    registry.register("fetch", handler, param_schema=schema)
    dispatcher = ToolDispatcher(registry)

    with pytest.raises(ToolInvocationError, match="schema validation"):
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(
                tool="fetch",
                args={},  # missing required `url`
                retry={"max_attempts": 5, "backoff_s": [0, 0, 0, 0, 0]},
                store_as="r",
            ),
            state={}, inputs={}, allowed_tools=["fetch"],
        )
    # Handler never ran (fail-closed) and no retry replay happened.
    assert calls["n"] == 0


async def test_caller_identity_forwarded_to_registry():
    """run_invocation threads task_type + agent_name into registry.dispatch."""
    seen: dict = {}

    class _RecordingRegistry(ToolRegistry):
        async def dispatch(  # type: ignore[override]
            self, tool_name, args, allowed_tools, *,
            task_type=None, agent_name=None,
        ):
            seen["task_type"] = task_type
            seen["agent_name"] = agent_name
            return {"ok": True}

    registry = _RecordingRegistry()
    dispatcher = ToolDispatcher(registry)
    await dispatcher.run_invocation(
        spec=ToolInvocationSpec(tool="t", args={}, store_as="r"),
        state={}, inputs={}, allowed_tools=["t"],
        task_type="skill_step::cap::step", agent_name="cap",
    )
    assert seen == {"task_type": "skill_step::cap::step", "agent_name": "cap"}


async def test_dispatch_renders_nested_args():
    async def echo(**kwargs):
        return {"got": kwargs}

    registry = ToolRegistry()
    registry.register("echo", echo)
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="echo",
            args={
                "config": {"url": "{{ inputs.u }}", "timeout": 5},
                "tags": ["{{ inputs.t }}", "static"],
            },
            store_as="r",
        ),
        state={}, inputs={"u": "https://a.com", "t": "tag1"},
        allowed_tools=["echo"],
    )

    assert result["r"]["got"]["config"]["url"] == "https://a.com"
    assert result["r"]["got"]["tags"] == ["tag1", "static"]
