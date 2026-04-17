"""on_failure DSL (F-W2-D) — escalate | continue | fail_step | fail_skill.

Exercises the four DSL values end-to-end through SkillExecutor against a
broken tool, plus unit-level ToolDispatcher tests for the failure branches.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.executor import (
    SkillExecutor,
    SkillFailedError,
    StepFailedError,
)
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.tool_dispatch import (
    ToolDispatcher,
    ToolInvocationError,
    ToolInvocationSpec,
)
from donna.skills.tool_registry import ToolRegistry


# -------------------- fixtures & helpers --------------------


def _make_skill() -> SkillRow:
    return SkillRow(
        id="s1",
        capability_name="test_on_failure",
        current_version_id="v1",
        state="sandbox",
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_version(yaml_backbone: str) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1",
        skill_id="s1",
        version_number=1,
        yaml_backbone=yaml_backbone,
        step_content={
            "step2_llm": "State so far: {{ state | tojson }}",
        },
        output_schemas={
            "step2_llm": {"type": "object"},
        },
        created_by="seed",
        changelog=None,
        created_at=datetime.now(timezone.utc),
    )


def _registry_with_broken_tool() -> ToolRegistry:
    async def broken_tool(**kwargs):
        raise RuntimeError("tool broke")

    registry = ToolRegistry()
    registry.register("broken_tool", broken_tool)
    return registry


def _mock_meta():
    return MagicMock(
        invocation_id="inv-1", latency_ms=5,
        tokens_in=1, tokens_out=1, cost_usd=0.0,
    )


# -------------------- dispatcher-level unit tests --------------------


async def test_dispatcher_continue_returns_tool_error():
    """on_failure=continue swallows the error and injects {tool_error: ...}."""
    dispatcher = ToolDispatcher(_registry_with_broken_tool())

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="broken_tool", args={}, store_as="page",
            on_failure="continue",
        ),
        state={}, inputs={}, allowed_tools=["broken_tool"],
    )

    assert result == {"page": {"tool_error": "tool broke"}}


async def test_dispatcher_fail_step_raises_stepfailed():
    dispatcher = ToolDispatcher(_registry_with_broken_tool())

    with pytest.raises(StepFailedError) as excinfo:
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(
                tool="broken_tool", args={}, store_as="page",
                on_failure="fail_step",
            ),
            state={}, inputs={}, allowed_tools=["broken_tool"],
        )

    assert excinfo.value.step_name == "page"
    assert isinstance(excinfo.value.cause, ToolInvocationError)


async def test_dispatcher_fail_skill_raises_skillfailed():
    dispatcher = ToolDispatcher(_registry_with_broken_tool())

    with pytest.raises(SkillFailedError) as excinfo:
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(
                tool="broken_tool", args={}, store_as="page",
                on_failure="fail_skill",
            ),
            state={}, inputs={}, allowed_tools=["broken_tool"],
        )

    assert excinfo.value.step_name == "page"
    assert isinstance(excinfo.value.cause, ToolInvocationError)


async def test_dispatcher_escalate_is_default_and_raises_toolinvocationerror():
    """Without on_failure, ToolInvocationError bubbles up (existing behavior)."""
    dispatcher = ToolDispatcher(_registry_with_broken_tool())

    with pytest.raises(ToolInvocationError):
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(
                tool="broken_tool", args={}, store_as="page",
            ),
            state={}, inputs={}, allowed_tools=["broken_tool"],
        )


async def test_invalid_on_failure_value_rejected_by_spec():
    with pytest.raises(ValueError, match="invalid on_failure"):
        ToolInvocationSpec(
            tool="broken_tool", args={}, store_as="page",
            on_failure="bogus",
        )


# -------------------- executor-level end-to-end tests --------------------


async def test_continue_injects_tool_error_and_proceeds():
    """Step 1 has on_failure=continue; step 2 still runs and sees the error."""
    backbone = (
        "capability_name: test_on_failure\n"
        "version: 1\n"
        "steps:\n"
        "  - name: fetch\n"
        "    kind: tool\n"
        "    tools: [broken_tool]\n"
        "    tool_invocations:\n"
        "      - tool: broken_tool\n"
        "        args: {}\n"
        "        store_as: page\n"
        "        on_failure: continue\n"
        "  - name: step2_llm\n"
        "    kind: llm\n"
        "    prompt: step2_llm\n"
        "    output_schema: step2_llm\n"
        "final_output: '{{ state.step2_llm }}'\n"
    )

    router = AsyncMock()
    router.complete.return_value = ({"saw_error": True}, _mock_meta())

    executor = SkillExecutor(
        model_router=router,
        tool_registry=_registry_with_broken_tool(),
    )
    result = await executor.execute(
        skill=_make_skill(),
        version=_make_version(backbone),
        inputs={},
        user_id="nick",
    )

    assert result.status == "succeeded", result.error
    # step 1's state entry is the collected tool-invocation result dict.
    assert result.state["fetch"] == {"page": {"tool_error": "tool broke"}}
    # step 2 ran.
    assert result.state["step2_llm"] == {"saw_error": True}
    assert router.complete.await_count == 1


async def test_fail_step_halts_without_escalation():
    """on_failure=fail_step → status=failed, no escalation, later steps skipped."""
    backbone = (
        "capability_name: test_on_failure\n"
        "version: 1\n"
        "steps:\n"
        "  - name: fetch\n"
        "    kind: tool\n"
        "    tools: [broken_tool]\n"
        "    tool_invocations:\n"
        "      - tool: broken_tool\n"
        "        args: {}\n"
        "        store_as: page\n"
        "        on_failure: fail_step\n"
        "  - name: step2_llm\n"
        "    kind: llm\n"
        "    prompt: step2_llm\n"
        "    output_schema: step2_llm\n"
    )

    router = AsyncMock()
    router.complete.return_value = ({"unused": True}, _mock_meta())

    executor = SkillExecutor(
        model_router=router,
        tool_registry=_registry_with_broken_tool(),
    )
    result = await executor.execute(
        skill=_make_skill(),
        version=_make_version(backbone),
        inputs={},
        user_id="nick",
    )

    assert result.status == "failed"
    assert result.escalation_reason is None
    assert "step_failed" in (result.error or "")
    assert "tool broke" in (result.error or "")
    # Second step must NOT have run.
    assert router.complete.await_count == 0
    assert "step2_llm" not in result.state


async def test_fail_skill_aborts_entire_run():
    """on_failure=fail_skill → status=failed immediately, no escalation."""
    backbone = (
        "capability_name: test_on_failure\n"
        "version: 1\n"
        "steps:\n"
        "  - name: fetch\n"
        "    kind: tool\n"
        "    tools: [broken_tool]\n"
        "    tool_invocations:\n"
        "      - tool: broken_tool\n"
        "        args: {}\n"
        "        store_as: page\n"
        "        on_failure: fail_skill\n"
        "  - name: step2_llm\n"
        "    kind: llm\n"
        "    prompt: step2_llm\n"
        "    output_schema: step2_llm\n"
    )

    router = AsyncMock()

    executor = SkillExecutor(
        model_router=router,
        tool_registry=_registry_with_broken_tool(),
    )
    result = await executor.execute(
        skill=_make_skill(),
        version=_make_version(backbone),
        inputs={},
        user_id="nick",
    )

    assert result.status == "failed"
    assert result.escalation_reason is None
    assert "skill_failed" in (result.error or "")
    assert result.final_output == {"tool_error": "tool broke"}
    assert router.complete.await_count == 0


async def test_escalate_is_default_behavior():
    """Step without on_failure bubbles ToolInvocationError → escalated (existing path)."""
    backbone = (
        "capability_name: test_on_failure\n"
        "version: 1\n"
        "steps:\n"
        "  - name: fetch\n"
        "    kind: tool\n"
        "    tools: [broken_tool]\n"
        "    tool_invocations:\n"
        "      - tool: broken_tool\n"
        "        args: {}\n"
        "        store_as: page\n"
    )

    router = AsyncMock()
    executor = SkillExecutor(
        model_router=router,
        tool_registry=_registry_with_broken_tool(),
    )
    result = await executor.execute(
        skill=_make_skill(),
        version=_make_version(backbone),
        inputs={},
        user_id="nick",
    )

    # No triage is configured, so ToolInvocationError → status=escalated.
    assert result.status == "escalated"
    assert result.escalation_reason is not None
    assert "tool_exhausted" in result.escalation_reason
