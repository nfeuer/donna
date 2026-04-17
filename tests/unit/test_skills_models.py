from datetime import datetime, timezone
from donna.skills.models import SkillRow, SkillVersionRow, row_to_skill, row_to_skill_version


def test_skill_row_basic():
    s = SkillRow(
        id="s1", capability_name="product_watch", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    assert s.state == "sandbox"


def test_row_to_skill_version_parses_json():
    raw = (
        "v1", "s1", 1, "yaml: content",
        '{"step_a": "markdown"}', '{"step_a": {"type": "object"}}',
        "claude", "initial version", "2026-04-15T00:00:00+00:00",
    )
    version = row_to_skill_version(raw)
    assert version.version_number == 1
    assert version.step_content == {"step_a": "markdown"}
    assert version.output_schemas == {"step_a": {"type": "object"}}
