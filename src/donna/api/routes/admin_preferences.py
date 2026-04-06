"""Preference management endpoints for the Donna Management GUI.

View and manage learned preference rules, browse correction history,
and toggle rule enabled/disabled state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


class RuleToggleBody(BaseModel):
    enabled: bool


def _parse_json_field(val: Any) -> Any:
    """Safely parse a JSON text field."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return val
    return val


@router.get("/preferences/rules")
async def list_preference_rules(
    request: Request,
    enabled: bool | None = Query(default=None),
    rule_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List learned preference rules with optional filters."""
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if enabled is not None:
        where_clauses.append("enabled = ?")
        params.append(enabled)
    if rule_type:
        where_clauses.append("rule_type = ?")
        params.append(rule_type)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM learned_preferences WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""SELECT id, user_id, rule_type, rule_text, confidence,
                   condition, action, supporting_corrections,
                   enabled, created_at, disabled_at
            FROM learned_preferences
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    rows = await cursor.fetchall()

    rules = []
    for row in rows:
        supporting = _parse_json_field(row[7])
        rules.append({
            "id": row[0],
            "user_id": row[1],
            "rule_type": row[2],
            "rule_text": row[3],
            "confidence": float(row[4]) if row[4] is not None else 0.0,
            "condition": _parse_json_field(row[5]),
            "action": _parse_json_field(row[6]),
            "supporting_corrections": supporting if isinstance(supporting, list) else [],
            "enabled": bool(row[8]),
            "created_at": row[9],
            "disabled_at": row[10],
        })

    return {
        "rules": rules,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/preferences/rules/{rule_id}")
async def toggle_preference_rule(
    request: Request,
    rule_id: str,
    body: RuleToggleBody,
) -> dict[str, Any]:
    """Toggle a preference rule's enabled state."""
    conn = request.app.state.db.connection

    cursor = await conn.execute(
        "SELECT id FROM learned_preferences WHERE id = ?", (rule_id,)
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Rule not found")

    disabled_at = None if body.enabled else datetime.now(timezone.utc).isoformat()

    await conn.execute(
        "UPDATE learned_preferences SET enabled = ?, disabled_at = ? WHERE id = ?",
        (body.enabled, disabled_at, rule_id),
    )
    await conn.commit()

    # Return the updated rule
    cursor = await conn.execute(
        """SELECT id, user_id, rule_type, rule_text, confidence,
                  condition, action, supporting_corrections,
                  enabled, created_at, disabled_at
           FROM learned_preferences WHERE id = ?""",
        (rule_id,),
    )
    row = await cursor.fetchone()
    supporting = _parse_json_field(row[7])

    return {
        "id": row[0],
        "user_id": row[1],
        "rule_type": row[2],
        "rule_text": row[3],
        "confidence": float(row[4]) if row[4] is not None else 0.0,
        "condition": _parse_json_field(row[5]),
        "action": _parse_json_field(row[6]),
        "supporting_corrections": supporting if isinstance(supporting, list) else [],
        "enabled": bool(row[8]),
        "created_at": row[9],
        "disabled_at": row[10],
    }


@router.get("/preferences/corrections")
async def list_corrections(
    request: Request,
    field: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated correction log with optional filters."""
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if field:
        where_clauses.append("field_corrected = ?")
        params.append(field)
    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM correction_log WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""SELECT id, timestamp, user_id, task_type, task_id,
                   input_text, field_corrected, original_value,
                   corrected_value, rule_extracted
            FROM correction_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    rows = await cursor.fetchall()

    corrections = [
        {
            "id": row[0],
            "timestamp": row[1],
            "user_id": row[2],
            "task_type": row[3],
            "task_id": row[4],
            "input_text": row[5],
            "field_corrected": row[6],
            "original_value": row[7],
            "corrected_value": row[8],
            "rule_extracted": row[9],
        }
        for row in rows
    ]

    return {
        "corrections": corrections,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/preferences/stats")
async def preference_stats(
    request: Request,
) -> dict[str, Any]:
    """Aggregate preference and correction statistics."""
    conn = request.app.state.db.connection

    # Rule counts
    cursor = await conn.execute("SELECT COUNT(*) FROM learned_preferences")
    total_rules = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM learned_preferences WHERE enabled = 1"
    )
    active_rules = (await cursor.fetchone())[0]
    disabled_rules = total_rules - active_rules

    # Average confidence
    cursor = await conn.execute(
        "SELECT AVG(confidence) FROM learned_preferences WHERE enabled = 1"
    )
    avg_row = await cursor.fetchone()
    avg_confidence = round(float(avg_row[0]), 4) if avg_row[0] is not None else None

    # Correction counts
    cursor = await conn.execute("SELECT COUNT(*) FROM correction_log")
    total_corrections = (await cursor.fetchone())[0]

    # Top corrected fields
    cursor = await conn.execute(
        """SELECT field_corrected, COUNT(*) AS cnt
           FROM correction_log
           GROUP BY field_corrected
           ORDER BY cnt DESC
           LIMIT 5"""
    )
    top_fields = [
        {"field": row[0], "count": int(row[1])}
        for row in await cursor.fetchall()
    ]

    return {
        "total_rules": total_rules,
        "active_rules": active_rules,
        "disabled_rules": disabled_rules,
        "avg_confidence": avg_confidence,
        "total_corrections": total_corrections,
        "top_fields": top_fields,
    }
