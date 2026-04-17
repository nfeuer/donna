"""Tests for F-W1-E: validation-mode per-step timeout."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from donna.config import SkillSystemConfig
from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_run_sink import ValidationRunSink


def _make_skill_and_version():
    skill = SkillRow(
        id="s1", capability_name="cap",
        current_version_id="v1", state="sandbox",
        requires_human_gate=False, baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=(
            "steps:\n"
            "  - name: parse\n"
            "    kind: llm\n"
            "    prompt: \"parse\"\n"
            "    output_schema:\n"
            "      type: object\n"
        ),
        step_content={"parse": "parse"},
        output_schemas={"parse": {"type": "object"}},
        created_by="test", changelog=None, created_at=None,
    )
    return skill, version


@pytest.mark.asyncio
async def test_per_step_timeout_fires_in_validation_mode() -> None:
    """When run_sink AND config are set, a slow router call raises and the
    executor surfaces a failed result."""
    async def slow_complete(**kwargs):
        await asyncio.sleep(3)
        return {}, MagicMock(invocation_id="x", cost_usd=0.0, latency_ms=1)

    router = MagicMock()
    router.complete = slow_complete

    executor = SkillExecutor(
        model_router=router,
        run_sink=ValidationRunSink(),
        config=SkillSystemConfig(validation_per_step_timeout_s=1),
    )
    skill, version = _make_skill_and_version()
    result = await executor.execute(
        skill=skill, version=version, inputs={}, user_id="test",
    )
    assert result.status in ("failed", "escalated")


@pytest.mark.asyncio
async def test_no_per_step_timeout_in_production_mode() -> None:
    """Without run_sink, a slow step should complete (no timeout enforced)."""
    call_count = {"n": 0}

    async def slow_complete(**kwargs):
        await asyncio.sleep(0.2)
        call_count["n"] += 1
        return {"ok": True}, MagicMock(invocation_id="x", cost_usd=0.0, latency_ms=1)

    router = MagicMock()
    router.complete = slow_complete

    # No run_sink. Config with aggressive timeout MUST be ignored.
    executor = SkillExecutor(
        model_router=router,
        config=SkillSystemConfig(validation_per_step_timeout_s=1),
    )
    skill, version = _make_skill_and_version()
    result = await executor.execute(
        skill=skill, version=version, inputs={}, user_id="test",
    )
    assert call_count["n"] >= 1
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_task_type_prefix_override() -> None:
    """task_type_prefix=skill_validation routes the step to skill_validation::cap::step."""
    captured_task_types = []

    async def capturing_complete(
        prompt, task_type, task_id=None, user_id="system",
    ):
        captured_task_types.append(task_type)

        class _Meta:
            invocation_id = "v"
            cost_usd = 0.0
            latency_ms = 1

        return {"ok": True}, _Meta()

    router = MagicMock()
    router.complete = capturing_complete

    executor = SkillExecutor(
        model_router=router,
        run_sink=ValidationRunSink(),
        config=SkillSystemConfig(),
        task_type_prefix="skill_validation",
    )
    skill, version = _make_skill_and_version()
    await executor.execute(skill=skill, version=version, inputs={}, user_id="test")

    assert any(tt.startswith("skill_validation::cap::") for tt in captured_task_types)
