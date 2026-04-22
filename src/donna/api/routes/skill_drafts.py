"""API routes for skill drafts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/skill-drafts")
async def list_skill_drafts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """List skills currently in draft state."""
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at "
        "FROM skill WHERE state = 'draft' ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    rows = list(await cursor.fetchall())
    return {"drafts": [_draft_to_dict(r) for r in rows], "count": len(rows)}


def _draft_to_dict(row: Sequence[Any]) -> dict[str, Any]:
    return {
        "id": row[0],
        "capability_name": row[1],
        "current_version_id": row[2],
        "state": row[3],
        "requires_human_gate": bool(row[4]),
        "baseline_agreement": row[5],
        "created_at": row[6],
        "updated_at": row[7],
    }
