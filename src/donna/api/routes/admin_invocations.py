"""Invocation log endpoints for the Donna Management GUI.

Browse and inspect individual LLM invocations with full output JSON,
linked task details, and filtering by task_type/model/shadow status.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/invocations")
async def list_invocations(
    request: Request,
    task_type: str | None = Query(default=None),
    model: str | None = Query(default=None, description="Filter by model_alias"),
    is_shadow: bool | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated invocation log with filters."""
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)
    if model:
        where_clauses.append("model_alias = ?")
        params.append(model)
    if is_shadow is not None:
        where_clauses.append("is_shadow = ?")
        params.append(is_shadow)
    if task_id:
        where_clauses.append("task_id = ?")
        params.append(task_id)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Safe: {where} is built from static column names; user values go through params
    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM invocation_log WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""SELECT id, timestamp, task_type, task_id, model_alias, model_actual,
                   latency_ms, tokens_in, tokens_out, cost_usd,
                   quality_score, is_shadow, spot_check_queued, user_id
            FROM invocation_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    rows = await cursor.fetchall()

    invocations = [
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
            "spot_check_queued": bool(row[12]),
            "user_id": row[13],
        }
        for row in rows
    ]

    return {
        "invocations": invocations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/invocations/{invocation_id}")
async def get_invocation(
    request: Request,
    invocation_id: str,
) -> dict[str, Any]:
    """Single invocation detail with full output JSON and linked task."""
    conn = request.app.state.db.connection

    cursor = await conn.execute(
        """SELECT id, timestamp, task_type, task_id, model_alias, model_actual,
                  input_hash, latency_ms, tokens_in, tokens_out, cost_usd,
                  output, quality_score, is_shadow, eval_session_id,
                  spot_check_queued, user_id
           FROM invocation_log WHERE id = ?""",
        (invocation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Invocation not found")

    output = None
    if row[11]:
        try:
            output = json.loads(row[11]) if isinstance(row[11], str) else row[11]
        except (ValueError, TypeError):
            output = {"raw": str(row[11])}

    invocation = {
        "id": row[0],
        "timestamp": row[1],
        "task_type": row[2],
        "task_id": row[3],
        "model_alias": row[4],
        "model_actual": row[5],
        "input_hash": row[6],
        "latency_ms": row[7],
        "tokens_in": row[8],
        "tokens_out": row[9],
        "cost_usd": float(row[10]),
        "output": output,
        "quality_score": float(row[12]) if row[12] is not None else None,
        "is_shadow": bool(row[13]),
        "eval_session_id": row[14],
        "spot_check_queued": bool(row[15]),
        "user_id": row[16],
    }

    # Fetch linked task if present
    linked_task = None
    if row[3]:
        cursor = await conn.execute(
            """SELECT id, title, status, domain, priority, created_at,
                      assigned_agent, agent_status
               FROM tasks WHERE id = ?""",
            (row[3],),
        )
        task_row = await cursor.fetchone()
        if task_row:
            linked_task = {
                "id": task_row[0],
                "title": task_row[1],
                "status": task_row[2],
                "domain": task_row[3],
                "priority": task_row[4],
                "created_at": task_row[5],
                "assigned_agent": task_row[6],
                "agent_status": task_row[7],
            }

    invocation["linked_task"] = linked_task
    return invocation
