"""API routes for skill candidate reports."""

from __future__ import annotations

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
async def dismiss_candidate(candidate_id: str, request: Request) -> dict:
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


@router.post("/skill-candidates/{candidate_id}/draft-now")
async def draft_candidate_now(candidate_id: str, request: Request) -> dict:
    """Trigger immediate auto-draft for this candidate (bypass nightly cron).

    After Wave 1 (F-6 process migration), ``auto_drafter`` lives only in the
    orchestrator process. The API can no longer invoke it directly. The
    endpoint returns 501 until a follow-up implements an IPC path (see
    followup F-W1-D in docs/superpowers/followups/...md).

    Existing callers can either (a) wait for the nightly cron, or (b) run
    ``donna draft --candidate-id <id>`` from the orchestrator (a future
    subcommand).

    Tests that provide their own ``request.app.state.auto_drafter`` (in-
    process doubles) continue to work — the 501 path is reached only when
    no drafter is present, which is the production state post-Wave 1.
    """
    conn = request.app.state.db.connection
    auto_drafter = getattr(request.app.state, "auto_drafter", None)

    if auto_drafter is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "draft-now runs in the orchestrator process after Wave 1 F-6 "
                "and has no HTTP trigger yet; see followup F-W1-D. Wait for "
                "the nightly cron or invoke from the orchestrator."
            ),
        )

    # Load the candidate.
    cursor = await conn.execute(
        "SELECT id, capability_name, task_pattern_hash, expected_savings_usd, "
        "volume_30d, variance_score, status, reported_at, resolved_at "
        "FROM skill_candidate_report WHERE id = ?",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    if row[6] != "new":
        raise HTTPException(
            status_code=409,
            detail=f"candidate status must be 'new' (is '{row[6]}')",
        )

    from donna.skills.candidate_report import row_to_candidate_report

    candidate = row_to_candidate_report(row)

    report = await auto_drafter.draft_one(candidate)
    return {
        "candidate_id": candidate_id,
        "outcome": report.outcome,
        "skill_id": report.skill_id,
        "pass_rate": report.pass_rate,
        "rationale": report.rationale,
    }


def _row_to_candidate_dict(row) -> dict:
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
