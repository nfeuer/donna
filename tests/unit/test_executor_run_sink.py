"""Tests for SkillExecutor.run_sink override."""

from __future__ import annotations

import pytest

from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_run_sink import ValidationRunSink

_YAML_BACKBONE = (
    "capability_name: cap\n"
    "version: 1\n"
    "steps:\n"
    "  - name: extract\n"
    "    kind: llm\n"
    "    prompt: steps/extract.md\n"
    "    output_schema: schemas/extract_v1.json\n"
)


@pytest.fixture
def fake_router():
    class _FakeRouter:
        async def complete(self, **kwargs):
            class _Meta:
                invocation_id = "inv"
                cost_usd = 0.0
                latency_ms = 1
            return {}, _Meta()
    return _FakeRouter()


@pytest.mark.asyncio
async def test_executor_delegates_to_run_sink_when_provided(fake_router) -> None:
    """When run_sink is set, the executor must not touch run_repository."""
    sink = ValidationRunSink()

    class FailingRepo:
        async def start_run(self, *args, **kwargs):
            raise AssertionError("run_repository must not be called when run_sink is set")
        async def record_step(self, *args, **kwargs):
            raise AssertionError("run_repository must not be called when run_sink is set")
        async def finish_run(self, *args, **kwargs):
            raise AssertionError("run_repository must not be called when run_sink is set")

    executor = SkillExecutor(
        model_router=fake_router,
        run_repository=FailingRepo(),
        run_sink=sink,
    )
    skill = SkillRow(
        id="s1", capability_name="cap", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=_YAML_BACKBONE,
        step_content={"extract": "Extract info"},
        output_schemas={"extract": {}},
        created_by="test", changelog=None, created_at=None,
    )
    await executor.execute(skill=skill, version=version, inputs={}, user_id="test")
    assert sink.run_id is not None
    assert sink.final_status in ("succeeded", "failed", "escalated")


@pytest.mark.asyncio
async def test_executor_without_run_sink_uses_run_repository(fake_router) -> None:
    """Regression test: when run_sink is None, behavior is unchanged."""
    captured = []

    class CapturingRepo:
        async def start_run(self, *args, **kwargs):
            captured.append(("start", args, kwargs))
            return "real-run-id"
        async def record_step(self, *args, **kwargs):
            captured.append(("step", args, kwargs))
            return "real-step-id"
        async def finish_run(self, *args, **kwargs):
            captured.append(("finish", args, kwargs))

    executor = SkillExecutor(
        model_router=fake_router,
        run_repository=CapturingRepo(),
    )
    skill = SkillRow(
        id="s1", capability_name="cap", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=_YAML_BACKBONE,
        step_content={"extract": "Extract info"},
        output_schemas={"extract": {}},
        created_by="test", changelog=None, created_at=None,
    )
    await executor.execute(skill=skill, version=version, inputs={}, user_id="test")
    # Repo was used (start and finish at minimum).
    assert any(c[0] == "start" for c in captured)
    assert any(c[0] == "finish" for c in captured)
