from datetime import UTC, datetime

from donna.tasks.db_models import SkillFixture, SkillRun, SkillStepResult


def test_skill_run_construction():
    run = SkillRun(
        id="r1", skill_id="s1", skill_version_id="v1",
        task_id=None, automation_run_id=None,
        status="running", total_latency_ms=None, total_cost_usd=None,
        state_object={}, tool_result_cache=None, final_output=None,
        escalation_reason=None, error=None,
        user_id="nick",
        started_at=datetime.now(UTC), finished_at=None,
    )
    assert run.status == "running"
    assert run.state_object == {}


def test_skill_step_result_construction():
    step = SkillStepResult(
        id="sr1", skill_run_id="r1", step_name="extract", step_index=0,
        step_kind="llm", invocation_log_id="inv-1",
        prompt_tokens=100, output={"title": "x"},
        tool_calls=None, latency_ms=50,
        validation_status="valid", error=None,
        created_at=datetime.now(UTC),
    )
    assert step.step_name == "extract"


def test_skill_fixture_construction():
    fix = SkillFixture(
        id="f1", skill_id="s1", case_name="basic",
        input={"raw_text": "hello"},
        expected_output_shape={"title": "string"},
        source="human_written", captured_run_id=None,
        created_at=datetime.now(UTC),
    )
    assert fix.source == "human_written"
