"""Read-only API routes for skill runs and step results."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from donna.skills.runs import (
    SELECT_SKILL_RUN, SELECT_SKILL_STEP_RESULT,
    row_to_skill_run, row_to_step_result,
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
