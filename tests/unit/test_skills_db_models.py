from datetime import UTC, datetime

from donna.tasks.db_models import (
    Capability,
    Skill,
    SkillState,
    TriggerType,
)


def test_capability_construction():
    cap = Capability(
        id="11111111-1111-1111-1111-111111111111",
        name="product_watch",
        description="Monitor a product page",
        input_schema={"type": "object", "properties": {}},
        trigger_type=TriggerType.ON_SCHEDULE,
        status="active",
        embedding=None,
        created_at=datetime.now(UTC),
        created_by="seed",
    )
    assert cap.name == "product_watch"
    assert cap.trigger_type == TriggerType.ON_SCHEDULE


def test_skill_construction():
    skill = Skill(
        id="22222222-2222-2222-2222-222222222222",
        capability_name="product_watch",
        current_version_id=None,
        state=SkillState.DRAFT,
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert skill.state == SkillState.DRAFT


def test_skill_state_values():
    assert SkillState.CLAUDE_NATIVE.value == "claude_native"
    assert SkillState.SKILL_CANDIDATE.value == "skill_candidate"
    assert SkillState.DRAFT.value == "draft"
    assert SkillState.SANDBOX.value == "sandbox"
    assert SkillState.SHADOW_PRIMARY.value == "shadow_primary"
    assert SkillState.TRUSTED.value == "trusted"
    assert SkillState.FLAGGED_FOR_REVIEW.value == "flagged_for_review"
    assert SkillState.DEGRADED.value == "degraded"
