from datetime import datetime, timezone

from donna.skills.runs import (
    SkillRunRow, SkillStepResultRow,
    row_to_skill_run, row_to_step_result,
)


def test_skill_run_row_basic():
    row = SkillRunRow(
        id="r1", skill_id="s1", skill_version_id="v1",
        task_id=None, automation_run_id=None,
        status="succeeded", total_latency_ms=100, total_cost_usd=0.0,
        state_object={"extract": {"title": "x"}}, tool_result_cache=None,
        final_output={"title": "x"}, escalation_reason=None, error=None,
        user_id="nick",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    assert row.status == "succeeded"


def test_row_to_skill_run_parses_json_fields():
    raw = (
        "r1", "s1", "v1", None, None, "succeeded", 100, 0.0,
        '{"extract": {"title": "x"}}', None,
        '{"title": "x"}', None, None, "nick",
        "2026-04-15T00:00:00+00:00", "2026-04-15T00:00:01+00:00",
    )
    run = row_to_skill_run(raw)
    assert run.state_object == {"extract": {"title": "x"}}
    assert run.final_output == {"title": "x"}
    assert run.started_at.year == 2026


def test_row_to_step_result_parses_tool_calls():
    raw = (
        "sr1", "r1", "extract", 0, "llm", "inv-1",
        100, '{"title": "x"}', '[{"tool": "web_fetch", "args": {"url": "x"}}]',
        50, "valid", None, "2026-04-15T00:00:00+00:00",
    )
    step = row_to_step_result(raw)
    assert step.output == {"title": "x"}
    assert step.tool_calls == [{"tool": "web_fetch", "args": {"url": "x"}}]
