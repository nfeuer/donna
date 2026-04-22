"""API routes for skill candidate reports."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/skill-candidates")
async def list_skill_candidates(
    request: Request,
    status: str | None = Query(default="new"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """List skill candidate reports, optionally filtered by status."""
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT id, capability_name, task_pattern_hash, expected_savings_usd, "
        "volume_30d, variance_score, status, reported_at, resolved_at "
        "FROM skill_candidate_report "
        + ("WHERE status = ? " if status else "")
        + "ORDER BY expected_savings_usd DESC, reported_at DESC LIMIT ?",
        tuple(x for x in ((status, limit) if status else (limit,)) if x is not None),
    )
    rows = await cursor.fetchall()
    return {
        "candidates": [_row_to_candidate_dict(r) for r in rows],
        "count": len(rows),
    }


@router.post("/skill-candidates/{candidate_id}/dismiss")
async def dismiss_candidate(candidate_id: str, request: Request) -> dict[str, Any]:
    """Mark a candidate as dismissed (no skill will be drafted)."""
    from donna.skills.candidate_report import SkillCandidateRepository

    conn = request.app.state.db.connection
    repo = SkillCandidateRepository(conn)

    # Check existence first.
    cursor = await conn.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    await repo.mark_dismissed(candidate_id)
    return {"candidate_id": candidate_id, "status": "dismissed"}


@router.post("/skill-candidates/{candidate_id}/draft-now", status_code=202)
async def draft_candidate_now(candidate_id: str, request: Request) -> dict[str, Any]:
    """Schedule the candidate for immediate drafting.

    Sets skill_candidate_report.manual_draft_at to now. The orchestrator's
    ManualDraftPoller picks it up on its 15s poll and runs AutoDrafter.
    Returns 202 Accepted — the actual draft runs asynchronously.

    After Wave 2 F-W1-D — see
    docs/superpowers/specs/archive/2026-04-17-skill-system-wave-2-first-capability-design.md.
    """
    from datetime import datetime

    conn = request.app.state.db.connection
    now_iso = datetime.now(tz=UTC).isoformat()
    cursor = await conn.execute(
        "UPDATE skill_candidate_report SET manual_draft_at = ? "
        "WHERE id = ? AND status = 'new'",
        (now_iso, candidate_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="candidate not found or not in 'new' status",
        )
    await conn.commit()
    return {"status": "scheduled", "manual_draft_at": now_iso}


def _row_to_candidate_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "capability_name": row[1],
        "task_pattern_hash": row[2],
        "expected_savings_usd": row[3],
        "volume_30d": row[4],
        "variance_score": row[5],
        "status": row[6],
        "reported_at": row[7],
        "resolved_at": row[8],
    }
