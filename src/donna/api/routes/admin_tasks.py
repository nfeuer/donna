"""Extended task endpoints for the Donna Management GUI.

Provides admin-level task views with additional fields (agent_status,
nudge_count, quality_score) and linked entities (invocations, nudges,
corrections, subtasks) not exposed in the Flutter API.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Query, Request

from donna.api.auth import admin_router

router = admin_router()


@router.get("/tasks")
async def list_tasks_admin(
    request: Request,
    status: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    priority: int | None = Query(default=None, ge=1, le=5),
    search: str | None = Query(default=None),
    agent: str | None = Query(default=None, description="Filter by assigned_agent"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Extended task list with admin-specific fields."""
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if status:
        where_clauses.append("status = ?")
        params.append(status)
    if domain:
        where_clauses.append("domain = ?")
        params.append(domain)
    if priority is not None:
        where_clauses.append("priority = ?")
        params.append(priority)
    if search:
        where_clauses.append("(title LIKE ? OR description LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if agent:
        where_clauses.append("assigned_agent = ?")
        params.append(agent)

    # Safe: {where} is built from static column names; user values go through params
    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""SELECT id, user_id, title, description, domain, priority, status,
                   estimated_duration, deadline, deadline_type, scheduled_start,
                   actual_start, completed_at, parent_task, prep_work_flag,
                   agent_eligible, assigned_agent, agent_status, tags, notes,
                   reschedule_count, created_at, created_via, nudge_count,
                   quality_score, donna_managed
            FROM tasks
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    rows = await cursor.fetchall()

    tasks = []
    for row in rows:
        tags = None
        if row[18]:
            try:
                tags = json.loads(row[18])
            except (ValueError, TypeError):
                tags = None

        tasks.append({
            "id": row[0],
            "user_id": row[1],
            "title": row[2],
            "description": row[3],
            "domain": row[4],
            "priority": row[5],
            "status": row[6],
            "estimated_duration": row[7],
            "deadline": row[8],
            "deadline_type": row[9],
            "scheduled_start": row[10],
            "actual_start": row[11],
            "completed_at": row[12],
            "parent_task": row[13],
            "prep_work_flag": bool(row[14]),
            "agent_eligible": bool(row[15]),
            "assigned_agent": row[16],
            "agent_status": row[17],
            "tags": tags,
            "reschedule_count": row[20],
            "created_at": row[21],
            "created_via": row[22],
            "nudge_count": row[23],
            "quality_score": float(row[24]) if row[24] is not None else None,
            "donna_managed": bool(row[25]),
        })

    return {
        "tasks": tasks,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/tasks/{task_id}")
async def get_task_admin(
    request: Request,
    task_id: str,
) -> dict[str, Any]:
    """Full task detail with linked invocations, nudges, corrections, subtasks."""
    conn = request.app.state.db.connection

    # Task
    cursor = await conn.execute(
        """SELECT id, user_id, title, description, domain, priority, status,
                  estimated_duration, deadline, deadline_type, scheduled_start,
                  actual_start, completed_at, recurrence, dependencies, parent_task,
                  prep_work_flag, prep_work_instructions, agent_eligible,
                  assigned_agent, agent_status, tags, notes, reschedule_count,
                  created_at, created_via, estimated_cost, calendar_event_id,
                  donna_managed, nudge_count, quality_score
           FROM tasks WHERE id = ?""",
        (task_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    def _parse_json(val: Any) -> Any:
        if val is None:
            return None
        try:
            return json.loads(val) if isinstance(val, str) else val
        except (ValueError, TypeError):
            return None

    task = {
        "id": row[0],
        "user_id": row[1],
        "title": row[2],
        "description": row[3],
        "domain": row[4],
        "priority": row[5],
        "status": row[6],
        "estimated_duration": row[7],
        "deadline": row[8],
        "deadline_type": row[9],
        "scheduled_start": row[10],
        "actual_start": row[11],
        "completed_at": row[12],
        "recurrence": row[13],
        "dependencies": _parse_json(row[14]),
        "parent_task": row[15],
        "prep_work_flag": bool(row[16]),
        "prep_work_instructions": row[17],
        "agent_eligible": bool(row[18]),
        "assigned_agent": row[19],
        "agent_status": row[20],
        "tags": _parse_json(row[21]),
        "notes": _parse_json(row[22]),
        "reschedule_count": row[23],
        "created_at": row[24],
        "created_via": row[25],
        "estimated_cost": float(row[26]) if row[26] is not None else None,
        "calendar_event_id": row[27],
        "donna_managed": bool(row[28]),
        "nudge_count": row[29],
        "quality_score": float(row[30]) if row[30] is not None else None,
    }

    # Linked invocations
    cursor = await conn.execute(
        """SELECT id, timestamp, task_type, model_alias, latency_ms,
                  tokens_in, tokens_out, cost_usd, is_shadow
           FROM invocation_log WHERE task_id = ?
           ORDER BY timestamp DESC LIMIT 50""",
        (task_id,),
    )
    task["invocations"] = [
        {
            "id": r[0], "timestamp": r[1], "task_type": r[2],
            "model_alias": r[3], "latency_ms": r[4],
            "tokens_in": r[5], "tokens_out": r[6],
            "cost_usd": float(r[7]), "is_shadow": bool(r[8]),
        }
        for r in await cursor.fetchall()
    ]

    # Linked nudge events
    cursor = await conn.execute(
        """SELECT id, nudge_type, channel, escalation_tier,
                  message_text, llm_generated, created_at
           FROM nudge_events WHERE task_id = ?
           ORDER BY created_at DESC LIMIT 50""",
        (task_id,),
    )
    task["nudge_events"] = [
        {
            "id": r[0], "nudge_type": r[1], "channel": r[2],
            "escalation_tier": r[3], "message_text": r[4],
            "llm_generated": bool(r[5]), "created_at": r[6],
        }
        for r in await cursor.fetchall()
    ]

    # Linked corrections
    cursor = await conn.execute(
        """SELECT id, timestamp, field_corrected, original_value,
                  corrected_value, task_type, input_text
           FROM correction_log WHERE task_id = ?
           ORDER BY timestamp DESC LIMIT 50""",
        (task_id,),
    )
    task["corrections"] = [
        {
            "id": r[0], "timestamp": r[1], "field_corrected": r[2],
            "original_value": r[3], "corrected_value": r[4],
            "task_type": r[5], "input_text": r[6],
        }
        for r in await cursor.fetchall()
    ]

    # Subtasks
    cursor = await conn.execute(
        """SELECT id, title, status, priority, assigned_agent, agent_status
           FROM tasks WHERE parent_task = ?
           ORDER BY created_at""",
        (task_id,),
    )
    task["subtasks"] = [
        {
            "id": r[0], "title": r[1], "status": r[2],
            "priority": r[3], "assigned_agent": r[4], "agent_status": r[5],
        }
        for r in await cursor.fetchall()
    ]

    return task
