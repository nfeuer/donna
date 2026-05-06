"""Escalation workspace endpoints for the Donna Management GUI.

Realizes docs/superpowers/specs/manual-escalation.md §6.3(b): the
canonical place to view and submit escalations. Slice 19 ships:

- ``GET  /admin/escalations`` — list view, status-filtered, age-sorted.
- ``GET  /admin/escalations/{correlation_id}`` — detail view, including
  the full prompt body and the ``escalation_lifecycle`` audit timeline
  joined from ``invocation_log``.
- ``POST /admin/escalations/{correlation_id}/submit`` — mode-agnostic
  submit endpoint; accepts a payload validated against
  ``schemas/escalation_submission.json`` (discriminated by ``mode``).
  Mode-specific UI behavior (chat textarea, claude_code "Mark as built"
  modal) attaches in slices 20/21.

The submit endpoint uses an optimistic lock on ``status`` so racing
submissions (re-submit after validation failure, two browser tabs) get
a 409 instead of silently overwriting each other.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
from fastapi import HTTPException, Query, Request

from donna.api.auth import admin_router
from donna.cost.escalation_audit import (
    ESCALATION_TASK_TYPE,
    write_escalation_event,
)

router = admin_router()


# Status values are documented in spec §8: open|resolved|submitted|
# validated|failed|cancelled. ``resolved`` means a manual mode was
# picked but the user hasn't pasted back the answer yet.
_LIST_STATUSES = (
    "open",
    "resolved",
    "submitted",
    "validated",
    "failed",
    "cancelled",
)

# Spec §6.1 manual_iteration_limit default. The full config-driven
# resolution lands with the dashboard runtime overrides in slice 23;
# this constant is the floor every endpoint must respect today.
_MANUAL_ITERATION_LIMIT = 3


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3].parent
    / "schemas"
    / "escalation_submission.json"
)
_SUBMISSION_SCHEMA: dict[str, Any] | None = None


def _load_submission_schema() -> dict[str, Any]:
    global _SUBMISSION_SCHEMA
    if _SUBMISSION_SCHEMA is None:
        with open(_SCHEMA_PATH) as f:
            _SUBMISSION_SCHEMA = json.load(f)
    return _SUBMISSION_SCHEMA


def _row_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Project an escalation_request row to the list/summary response shape."""
    offered_modes = row.get("offered_modes")
    if isinstance(offered_modes, str):
        try:
            offered_modes = json.loads(offered_modes)
        except (TypeError, ValueError):
            offered_modes = []
    return {
        "id": row["id"],
        "correlation_id": row["correlation_id"],
        "user_id": row["user_id"],
        "task_id": row["task_id"],
        "task_type": row["task_type"],
        "estimate_usd": float(row["estimate_usd"]),
        "daily_remaining_usd": float(row["daily_remaining_usd"]),
        "offered_modes": offered_modes or [],
        "resolution": row["resolution"],
        "mode": row.get("mode"),
        "status": row["status"],
        "iteration": row["iteration"],
        "priority": row["priority"],
        "summary": row.get("summary"),
        "branch_name": row.get("branch_name"),
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "submitted_at": row["submitted_at"],
        "validated_at": row["validated_at"],
    }


def _row_dict(cursor: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row, strict=True))


@router.get("/escalations")
async def list_escalations(
    request: Request,
    status: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return open + recent escalations for the workspace list view.

    Sorted oldest-first within ``open`` rows so age pressure is visible
    at the top, then by ``created_at`` desc for the rest. Multi-user-ready
    via the ``user_id`` filter even though Phase 1 is single-user.
    """
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if status:
        if status not in _LIST_STATUSES:
            raise HTTPException(
                status_code=400, detail={"error": "invalid_status", "value": status}
            )
        where_clauses.append("status = ?")
        params.append(status)
    if user_id:
        where_clauses.append("user_id = ?")
        params.append(user_id)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM escalation_request WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""
        SELECT id, correlation_id, user_id, task_id, task_type,
               estimate_usd, daily_remaining_usd, offered_modes,
               resolution, mode, status, iteration, priority,
               summary, branch_name,
               created_at, resolved_at, submitted_at, validated_at
          FROM escalation_request
         WHERE {where}
         ORDER BY (status = 'open') DESC,
                  CASE WHEN status = 'open' THEN created_at END ASC,
                  created_at DESC
         LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()
    items = [_row_to_summary(_row_dict(cursor, r)) for r in rows]

    cursor = await conn.execute(
        "SELECT status, COUNT(*) FROM escalation_request GROUP BY status",
    )
    status_counts = {str(r[0]): int(r[1]) for r in await cursor.fetchall()}

    return {
        "items": items,
        "total": int(total),
        "status_counts": status_counts,
        "limit": limit,
        "offset": offset,
    }


@router.get("/escalations/{correlation_id}")
async def get_escalation(
    request: Request,
    correlation_id: str,
) -> dict[str, Any]:
    """Detail view: full prompt body, status, and audit timeline.

    The timeline pulls every ``escalation_lifecycle`` invocation_log row
    bound to this escalation_request_id. Each event's payload is the
    JSON written by :func:`donna.cost.escalation_audit.write_escalation_event`.
    """
    conn = request.app.state.db.connection

    cursor = await conn.execute(
        """
        SELECT id, correlation_id, user_id, task_id, task_type,
               estimate_usd, daily_remaining_usd, offered_modes,
               resolution, mode, status, iteration, priority,
               prompt_path, prompt_body, summary, result, validation_result,
               branch_name,
               created_at, resolved_at, submitted_at, validated_at
          FROM escalation_request
         WHERE correlation_id = ?
        """,
        (correlation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    record = _row_dict(cursor, row)

    summary = _row_to_summary(record)

    validation_result = record["validation_result"]
    if isinstance(validation_result, str):
        try:
            validation_result = json.loads(validation_result)
        except (TypeError, ValueError):
            validation_result = {"raw": validation_result}

    detail = {
        **summary,
        "prompt_path": record["prompt_path"],
        "prompt_body": record["prompt_body"],
        "result": record["result"],
        "validation_result": validation_result,
    }

    cursor = await conn.execute(
        """
        SELECT id, timestamp, output
          FROM invocation_log
         WHERE escalation_request_id = ? AND task_type = ?
         ORDER BY timestamp ASC
        """,
        (record["id"], ESCALATION_TASK_TYPE),
    )
    events: list[dict[str, Any]] = []
    for ev_row in await cursor.fetchall():
        raw_output = ev_row[2]
        try:
            payload = (
                json.loads(raw_output) if isinstance(raw_output, str) else raw_output
            )
        except (TypeError, ValueError):
            payload = {"raw": str(raw_output)}
        events.append({
            "id": ev_row[0],
            "timestamp": ev_row[1],
            "event": (payload or {}).get("event") if isinstance(payload, dict) else None,
            "payload": payload if isinstance(payload, dict) else {"raw": payload},
        })

    return {"escalation": detail, "timeline": events}


@router.post("/escalations/{correlation_id}/submit")
async def submit_escalation(
    request: Request,
    correlation_id: str,
) -> dict[str, Any]:
    """Accept the user's submission for a manual-handoff escalation.

    Mode-agnostic for slice 19: validates the payload against the
    discriminated-union schema, ensures ``mode`` matches the row's
    selected mode, and atomically transitions ``resolved → submitted``
    (or ``failed → submitted`` for re-submits within the iteration cap).

    Mode-specific UI behaviour (chat textarea, claude_code "Mark as built"
    modal) attaches in slices 20 and 21 — they POST the same payload to
    this endpoint.
    """
    schema = _load_submission_schema()
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_json", "message": str(exc)}
        ) from exc
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "schema_validation_failed", "message": exc.message},
        ) from exc

    conn = request.app.state.db.connection
    cursor = await conn.execute(
        """
        SELECT id, user_id, task_id, status, mode, iteration
          FROM escalation_request
         WHERE correlation_id = ?
        """,
        (correlation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    record = _row_dict(cursor, row)

    if record["status"] not in ("resolved", "failed"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_awaiting_submission",
                "status": record["status"],
            },
        )
    if record["mode"] is not None and record["mode"] != payload["mode"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "mode_mismatch",
                "expected_mode": record["mode"],
                "submitted_mode": payload["mode"],
            },
        )
    # Iteration cap (spec §6.1, §10.4 row 2). Refusing the next submit
    # keeps `iteration` bounded; cancel-on-cap-and-route-to-human is
    # slice 21 scope.
    if record["status"] == "failed" and int(record["iteration"]) >= _MANUAL_ITERATION_LIMIT:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "iteration_cap_reached",
                "iteration": int(record["iteration"]),
                "limit": _MANUAL_ITERATION_LIMIT,
            },
        )

    now = datetime.now(tz=UTC)
    ts = now.isoformat()
    branch = payload["branch"] if payload["mode"] == "claude_code" else None

    # The mode discriminator is folded into the WHERE so a concurrent
    # writer can't slip a wrong-mode update through between the SELECT
    # above and this UPDATE. The CASE on the iteration column reads
    # `status` BEFORE the SET (SQLite evaluates the right-hand side of
    # all SET expressions against the row's pre-update values), so this
    # increments only on the failed → submitted transition.
    update_cursor = await conn.execute(
        """
        UPDATE escalation_request
           SET status = 'submitted',
               submitted_at = ?,
               result = ?,
               branch_name = COALESCE(?, branch_name),
               iteration = iteration + CASE WHEN status = 'failed' THEN 1 ELSE 0 END,
               mode = COALESCE(mode, ?)
         WHERE correlation_id = ?
           AND status IN ('resolved', 'failed')
           AND (mode IS NULL OR mode = ?)
        """,
        (
            ts,
            json.dumps(payload),
            branch,
            payload["mode"],
            correlation_id,
            payload["mode"],
        ),
    )
    if update_cursor.rowcount == 0:
        # Lost the race — another submission or status change won.
        raise HTTPException(
            status_code=409,
            detail={"error": "concurrent_submission"},
        )
    await conn.commit()

    # Re-read to return the post-update view.
    cursor = await conn.execute(
        "SELECT iteration FROM escalation_request WHERE correlation_id = ?",
        (correlation_id,),
    )
    iteration = (await cursor.fetchone())[0]

    await write_escalation_event(
        conn,
        event="escalation_submitted",
        escalation_request_id=int(record["id"]),
        correlation_id=correlation_id,
        user_id=str(record["user_id"]),
        task_id=record["task_id"],
        payload={
            "mode": payload["mode"],
            "branch": branch,
            "iteration": int(iteration),
        },
        now=now,
    )

    return {
        "correlation_id": correlation_id,
        "status": "submitted",
        "submitted_at": ts,
        "iteration": int(iteration),
        "mode": payload["mode"],
    }
