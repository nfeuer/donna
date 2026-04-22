"""Read-only API routes for skill runs and step results."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from donna.skills.runs import (
    SELECT_SKILL_RUN,
    SELECT_SKILL_STEP_RESULT,
    row_to_skill_run,
    row_to_step_result,
)

router = APIRouter()


def _run_to_dict(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "skill_id": run.skill_id,
        "skill_version_id": run.skill_version_id,
        "status": run.status,
        "total_latency_ms": run.total_latency_ms,
        "total_cost_usd": run.total_cost_usd,
        "escalation_reason": run.escalation_reason,
        "error": run.error,
        "user_id": run.user_id,
        "started_at": str(run.started_at),
        "finished_at": str(run.finished_at) if run.finished_at else None,
    }


def _step_to_dict(step) -> dict[str, Any]:
    return {
        "id": step.id,
        "step_name": step.step_name,
        "step_index": step.step_index,
        "step_kind": step.step_kind,
        "output": step.output,
        "tool_calls": step.tool_calls,
        "latency_ms": step.latency_ms,
        "validation_status": step.validation_status,
        "error": step.error,
    }


@router.get("/skills/{skill_id}/runs")
async def list_runs_for_skill(
    skill_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        f"""
        SELECT {SELECT_SKILL_RUN} FROM skill_run
         WHERE skill_id = ?
         ORDER BY started_at DESC
         LIMIT ? OFFSET ?
        """,
        (skill_id, limit, offset),
    )
    rows = await cursor.fetchall()
    runs = [_run_to_dict(row_to_skill_run(r)) for r in rows]
    return {"runs": runs, "count": len(runs)}


@router.get("/skill-runs/{run_id}")
async def get_run_detail(
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    conn = request.app.state.db.connection

    cursor = await conn.execute(
        f"SELECT {SELECT_SKILL_RUN} FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill run '{run_id}' not found")
    run = row_to_skill_run(row)

    cursor = await conn.execute(
        f"""
        SELECT {SELECT_SKILL_STEP_RESULT} FROM skill_step_result
         WHERE skill_run_id = ?
         ORDER BY step_index ASC
        """,
        (run_id,),
    )
    step_rows = await cursor.fetchall()
    step_results = [_step_to_dict(row_to_step_result(r)) for r in step_rows]

    result = _run_to_dict(run)
    result["state_object"] = run.state_object
    result["final_output"] = run.final_output
    result["step_results"] = step_results
    return result


@router.get("/skill-runs")
async def list_recent_runs(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    if status:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL_RUN} FROM skill_run WHERE status = ? ORDER BY started_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL_RUN} FROM skill_run ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
    rows = await cursor.fetchall()
    runs = [_run_to_dict(row_to_skill_run(r)) for r in rows]
    return {"runs": runs, "count": len(runs)}


@router.get("/skill-runs/{skill_run_id}/divergence")
async def get_skill_run_divergence(skill_run_id: str, request: Request) -> dict:
    """Shadow divergence details for a skill run (if any)."""
    import json

    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT id, skill_run_id, shadow_invocation_id, overall_agreement, "
        "diff_summary, flagged_for_evolution, created_at "
        "FROM skill_divergence WHERE skill_run_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (skill_run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no divergence recorded")

    return {
        "id": row[0],
        "skill_run_id": row[1],
        "shadow_invocation_id": row[2],
        "overall_agreement": row[3],
        "diff_summary": json.loads(row[4]) if isinstance(row[4], str) else row[4],
        "flagged_for_evolution": bool(row[5]),
        "created_at": row[6],
    }


@router.post("/skill-runs/{run_id}/capture-fixture", status_code=201)
async def capture_fixture(run_id: str, request: Request) -> dict:
    """Capture a succeeded skill_run into a reusable skill_fixture row.

    Reads the run's final_output + tool_result_cache, infers a structural
    expected_output_shape via json_to_schema, synthesizes tool_mocks via
    cache_to_mocks, and inserts a skill_fixture(source='captured_from_run')
    row pointing at the run.
    """
    import json

    from donna.skills.auto_drafter import _persist_fixture
    from donna.skills.mock_synthesis import cache_to_mocks
    from donna.skills.schema_inference import json_to_schema

    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT id, skill_id, status, final_output, tool_result_cache, state_object "
        "FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="skill_run not found")
    if row[2] != "succeeded":
        raise HTTPException(
            status_code=409,
            detail="can only capture fixtures from succeeded runs",
        )

    final_output = json.loads(row[3]) if row[3] else {}
    cache = json.loads(row[4]) if row[4] else {}
    state_obj = json.loads(row[5]) if row[5] else {}
    inputs = state_obj.get("inputs", {}) if isinstance(state_obj, dict) else {}

    expected_shape = json_to_schema(final_output)
    tool_mocks = cache_to_mocks(cache)

    fixture_id = await _persist_fixture(
        conn=conn,
        skill_id=row[1],
        case_name=f"captured_from_{run_id[:8]}",
        input_=inputs,
        expected_output_shape=expected_shape,
        tool_mocks=tool_mocks if tool_mocks else None,
        source="captured_from_run",
        captured_run_id=run_id,
    )
    await conn.commit()
    return {"fixture_id": fixture_id, "source": "captured_from_run"}
