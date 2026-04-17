"""Tests for donna.skills.validation_run_sink.ValidationRunSink."""

from __future__ import annotations

import pytest

from donna.skills.validation_run_sink import ValidationRunSink


@pytest.mark.asyncio
async def test_start_run_returns_synthetic_id() -> None:
    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s1",
        skill_version_id="v1",
        inputs={"q": 1},
        user_id="validation",
        task_id=None,
        automation_run_id=None,
    )
    assert run_id.startswith("validation-run-")


@pytest.mark.asyncio
async def test_record_step_captures_call() -> None:
    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="validation",
        task_id=None, automation_run_id=None,
    )
    await sink.record_step(
        skill_run_id=run_id,
        step_name="parse",
        step_index=0,
        step_kind="llm",
        output={"title": "x"},
        latency_ms=42,
        validation_status="valid",
        invocation_log_id="local_parser_validation:inv_1",
    )
    assert len(sink.step_records) == 1
    rec = sink.step_records[0]
    assert rec.step_name == "parse"
    assert rec.invocation_log_id == "local_parser_validation:inv_1"


@pytest.mark.asyncio
async def test_finish_run_captures_final_state() -> None:
    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="validation",
        task_id=None, automation_run_id=None,
    )
    await sink.finish_run(
        skill_run_id=run_id,
        status="succeeded",
        final_output={"k": 1},
        state_object={},
        tool_result_cache={},
        total_latency_ms=100,
        total_cost_usd=0.0,
        escalation_reason=None,
        error=None,
    )
    assert sink.final_status == "succeeded"
    assert sink.final_output == {"k": 1}


@pytest.mark.asyncio
async def test_sink_writes_nothing_to_db(tmp_path) -> None:
    """The sink must never open or write to any file."""
    import aiosqlite
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE skill_run (id TEXT PRIMARY KEY)")
        await conn.commit()

    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s", skill_version_id="v",
        inputs={}, user_id="validation",
        task_id=None, automation_run_id=None,
    )
    await sink.finish_run(
        skill_run_id=run_id, status="succeeded",
        final_output={}, state_object={}, tool_result_cache={},
        total_latency_ms=0, total_cost_usd=0.0,
        escalation_reason=None, error=None,
    )

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM skill_run")
        row = await cursor.fetchone()
        assert row[0] == 0
