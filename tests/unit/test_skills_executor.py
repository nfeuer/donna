from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.models import SkillRow, SkillVersionRow


def _make_skill() -> SkillRow:
    return SkillRow(
        id="s1", capability_name="parse_task", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )


def _make_version(step_content: dict, output_schemas: dict, yaml_backbone: str | None = None) -> SkillVersionRow:
    if yaml_backbone is None:
        step_names = list(step_content.keys())
        steps_yaml = "\n".join(
            f"  - name: {name}\n    kind: llm\n    prompt: steps/{name}.md\n    output_schema: schemas/{name}_v1.json"
            for name in step_names
        )
        yaml_backbone = f"capability_name: parse_task\nversion: 1\nsteps:\n{steps_yaml}\nfinal_output: '{{{{ state.{step_names[0]} }}}}'"

    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1, yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None, created_at=datetime.now(timezone.utc),
    )


def _mock_meta(**kwargs):
    defaults = {"invocation_id": "inv-1", "latency_ms": 100, "tokens_in": 50, "tokens_out": 20, "cost_usd": 0.0}
    defaults.update(kwargs)
    return MagicMock(**defaults)


async def test_executor_runs_single_step_skill():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["title", "confidence"],
        }},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"title": "Draft Q2 review", "confidence": 0.9},
        _mock_meta(),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "draft the Q2 review by Friday"},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.final_output == {"title": "Draft Q2 review", "confidence": 0.9}
    assert "extract" in result.state
    assert result.state["extract"]["title"] == "Draft Q2 review"


async def test_executor_handles_escalate_signal():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "escalate": {"type": "object"},
            },
        }},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"escalate": {"reason": "insufficient info"}},
        _mock_meta(invocation_id="inv-2"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "???"},
        user_id="nick",
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "insufficient info"


async def test_executor_fails_on_schema_validation_error():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"not_title": "x"},
        _mock_meta(invocation_id="inv-3"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "foo"},
        user_id="nick",
    )

    assert result.status == "failed"
    assert "title" in result.error


async def test_executor_fails_on_model_exception():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {"type": "object"}},
    )

    router = AsyncMock()
    router.complete.side_effect = RuntimeError("model_unavailable")

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "foo"},
        user_id="nick",
    )

    assert result.status == "failed"
    assert "model_call" in result.error


async def test_executor_handles_empty_steps():
    version = _make_version(
        step_content={},
        output_schemas={},
        yaml_backbone="capability_name: parse_task\nversion: 1\nsteps: []\n",
    )

    router = AsyncMock()
    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.final_output == {}
    router.complete.assert_not_called()
