"""Claude Inspector endpoints for the Donna Management GUI.

Browse LLM invocations with advanced filtering, retrieve full
request/response payloads, and compute cost/quality insights.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query, Request

from donna.api.auth import admin_router

router = admin_router()

_SORTABLE_COLUMNS = {
    "timestamp": "timestamp",
    "cost": "cost_usd",
    "tokens_in": "tokens_in",
    "tokens_out": "tokens_out",
    "latency": "latency_ms",
}


@router.get("/claude/calls")
async def get_calls(
    request: Request,
    task_type: str | None = Query(default=None),
    model: str | None = Query(default=None, description="Filter by model_alias"),
    date_from: str | None = Query(default=None, description="ISO date string lower bound"),
    date_to: str | None = Query(default=None, description="ISO date string upper bound"),
    min_cost: float | None = Query(default=None),
    min_tokens_in: int | None = Query(default=None),
    quality_score_below: float | None = Query(default=None),
    sort: str = Query(default="timestamp", description="Sort column"),
    sort_dir: str = Query(default="desc", description="Sort direction"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated call browser with filters."""
    conn = request.app.state.db.connection
    payload_dir: Path = request.app.state.payload_dir

    where_clauses: list[str] = []
    params: list[Any] = []

    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)
    if model:
        where_clauses.append("model_alias = ?")
        params.append(model)
    if date_from:
        where_clauses.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("timestamp <= ?")
        params.append(date_to)
    if min_cost is not None:
        where_clauses.append("cost_usd >= ?")
        params.append(min_cost)
    if min_tokens_in is not None:
        where_clauses.append("tokens_in >= ?")
        params.append(min_tokens_in)
    if quality_score_below is not None:
        where_clauses.append("quality_score < ?")
        params.append(quality_score_below)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Validate sort parameters
    if sort not in _SORTABLE_COLUMNS:
        sort = "timestamp"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    order_col = _SORTABLE_COLUMNS[sort]
    order_dir = sort_dir.upper()

    # Count query
    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM invocation_log WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    # Data query
    cursor = await conn.execute(
        f"""SELECT id, timestamp, task_type, task_id, model_alias, model_actual,
                   latency_ms, tokens_in, tokens_out, cost_usd,
                   quality_score, is_shadow, user_id,
                   estimated_tokens_in, overflow_escalated, payload_path
            FROM invocation_log
            WHERE {where}
            ORDER BY {order_col} {order_dir}
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()

    calls = []
    for row in rows:
        payload_path_val = row[15]
        has_payload = False
        if payload_path_val:
            has_payload = (payload_dir / payload_path_val).exists()

        calls.append(
            {
                "id": row[0],
                "timestamp": row[1],
                "task_type": row[2],
                "task_id": row[3],
                "model_alias": row[4],
                "model_actual": row[5],
                "latency_ms": row[6],
                "tokens_in": row[7],
                "tokens_out": row[8],
                "cost_usd": float(row[9]),
                "quality_score": float(row[10]) if row[10] is not None else None,
                "is_shadow": bool(row[11]),
                "user_id": row[12],
                "estimated_tokens_in": row[13],
                "overflow_escalated": bool(row[14]),
                "payload_path": payload_path_val,
                "has_payload": has_payload,
            }
        )

    return {
        "calls": calls,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/claude/calls/{invocation_id}/payload")
async def get_payload(
    request: Request,
    invocation_id: str,
) -> dict[str, Any]:
    """Return full request/response JSON from the payload file."""
    conn = request.app.state.db.connection
    payload_dir: Path = request.app.state.payload_dir

    cursor = await conn.execute(
        "SELECT payload_path FROM invocation_log WHERE id = ?",
        (invocation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Invocation not found")

    payload_path = row[0]
    if not payload_path:
        raise HTTPException(status_code=404, detail="Payload path is null")

    full_path = payload_dir / payload_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Payload file not found on disk")

    content = full_path.read_text(encoding="utf-8")
    return json.loads(content)


@router.get("/claude/insights")
async def get_insights(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Compute cost/quality insights over recent invocations."""
    from donna.insights.engine import compute_insights

    conn = request.app.state.db.connection
    payload_dir: Path = request.app.state.payload_dir

    return await compute_insights(conn=conn, payload_dir=payload_dir, days=days)
