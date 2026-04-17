"""F-W1-G regression: ValidationExecutor tags LLM calls skill_validation::..."""

from __future__ import annotations

import pytest

from donna.config import SkillSystemConfig
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_executor import ValidationExecutor


@pytest.mark.asyncio
async def test_validation_run_tags_task_type_with_skill_validation_prefix() -> None:
    """ValidationExecutor must prefix step task_types with skill_validation:: so
    invocation_log rows are filterable (spec §6.1, F-W1-G)."""
    captured_task_types: list[str] = []

    class _CapturingRouter:
        async def complete(
            self,
            prompt: str,
            task_type: str,
            task_id: str | None = None,
            user_id: str = "system",
        ):
            captured_task_types.append(task_type)

            class _Meta:
                invocation_id = "v"
                cost_usd = 0.0
                latency_ms = 1

            return {"ok": True}, _Meta()

    ve = ValidationExecutor(
        model_router=_CapturingRouter(),
        config=SkillSystemConfig(),
    )
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

    await ve.execute(
        skill=skill, version=version, inputs={}, user_id="test", tool_mocks=None,
    )

    # At least one call tagged skill_validation::cap::...
    assert any(
        tt.startswith("skill_validation::cap::") for tt in captured_task_types
    ), f"Expected skill_validation:: prefix; got: {captured_task_types}"

    # Importantly: NO skill_step:: prefix in validation runs.
    assert not any(
        tt.startswith("skill_step::") for tt in captured_task_types
    ), f"ValidationExecutor must not emit skill_step:: prefix; got: {captured_task_types}"
