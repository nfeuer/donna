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
  Slice 20 wires the chat-mode entry path; slice 21 wires claude_code.

Slice 21 adds:
- ``POST /admin/escalations/{correlation_id}/mark-merged`` — pure
  tracking write the user clicks AFTER they've manually merged the
  validated branch into ``main``. Donna never auto-merges (spec §15);
  this endpoint just flips ``merged_at`` so the dashboard reflects the
  state.

The submit endpoint uses an optimistic lock on ``status`` so racing
submissions (re-submit after validation failure, two browser tabs) get
a 409 instead of silently overwriting each other. Slice 20 factored the
heavy lifting into :mod:`donna.cost.escalation_submit_service` so the
``/donna submit`` Discord slash command can reuse it without copying
validation or audit logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Query, Request

from donna.api.auth import admin_router
from donna.cost.escalation_audit import ESCALATION_TASK_TYPE
from donna.cost.escalation_submit_service import (
    SubmissionError,
    apply_submission,
)
from donna.cost.tool_gap_audit import TOOL_GAP_TASK_TYPE

router = admin_router()


# Slice 24 — task_types whose audit rows are joined onto the per-row
# timeline. ``escalation_lifecycle`` covers the slice-17 lifecycle;
# ``tool_gap_lifecycle`` covers slice-22 audit rows for tool builds
# (``tool_request_filed`` / ``tool_request_filled`` / etc.) that share
# the escalation_request_id when an escalation drives the tool build.
# Spec §10.10 — every state transition is on the timeline regardless of
# task_type.
_TIMELINE_TASK_TYPES = (ESCALATION_TASK_TYPE, TOOL_GAP_TASK_TYPE)


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
        "human_review": bool(row.get("human_review", 0)),
        "merged_at": row.get("merged_at"),
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
               summary, branch_name, human_review, merged_at,
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
               branch_name, human_review, merged_at,
               target_paths, originating_entity_type, originating_entity_id,
               base_sha,
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

    target_paths = record.get("target_paths")
    if isinstance(target_paths, str):
        try:
            target_paths = json.loads(target_paths)
        except (TypeError, ValueError):
            target_paths = None

    detail = {
        **summary,
        "prompt_path": record["prompt_path"],
        "prompt_body": record["prompt_body"],
        "result": record["result"],
        "validation_result": validation_result,
        "target_paths": target_paths,
        "originating_entity_type": record.get("originating_entity_type"),
        "originating_entity_id": record.get("originating_entity_id"),
        "base_sha": record.get("base_sha"),
    }

    events = await _fetch_timeline(conn, escalation_request_id=record["id"])

    return {"escalation": detail, "timeline": events}


async def _fetch_timeline(
    conn: Any,
    *,
    escalation_request_id: int,
    after_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return chronologically-ordered audit rows for one escalation.

    Joins ``escalation_lifecycle`` (slice 17) and ``tool_gap_lifecycle``
    (slice 22) rows so a single response covers the full lifecycle of
    both standard escalations and tool-build escalations. Spec §10.10:
    *every* state transition lands on the timeline.

    Args:
        conn: aiosqlite connection from app state.
        escalation_request_id: FK on ``invocation_log`` to filter.
        after_id: Optional cursor — return rows whose ``id`` sorts
            after this (UUIDv7 sorts lexicographically by time).
        limit: Optional ``LIMIT`` cap for paginated polling.

    Returns:
        List of ``{id, timestamp, task_type, event, payload}`` rows.
    """
    placeholders = ",".join("?" * len(_TIMELINE_TASK_TYPES))
    sql = (
        "SELECT id, timestamp, task_type, output "
        "FROM invocation_log "
        f"WHERE escalation_request_id = ? AND task_type IN ({placeholders})"
    )
    params: list[Any] = [escalation_request_id, *_TIMELINE_TASK_TYPES]
    if after_id is not None:
        sql += " AND id > ?"
        params.append(after_id)
    sql += " ORDER BY timestamp ASC, id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    cursor = await conn.execute(sql, params)
    events: list[dict[str, Any]] = []
    for ev_row in await cursor.fetchall():
        raw_output = ev_row[3]
        try:
            payload = (
                json.loads(raw_output) if isinstance(raw_output, str) else raw_output
            )
        except (TypeError, ValueError):
            payload = {"raw": str(raw_output)}
        event_name = (
            payload.get("event") if isinstance(payload, dict) else None
        )
        events.append({
            "id": ev_row[0],
            "timestamp": ev_row[1],
            "task_type": ev_row[2],
            "event": event_name,
            "payload": payload if isinstance(payload, dict) else {"raw": payload},
        })
    return events


@router.get("/escalations/{correlation_id}/timeline")
async def get_escalation_timeline(
    request: Request,
    correlation_id: str,
    after_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Standalone timeline endpoint for live-refresh on the detail page.

    Slice 24 (spec §10.10). Unlike the embedded timeline in the detail
    response, this can be polled independently and supports an
    ``after_id`` cursor so the dashboard can append new events without
    re-rendering history.

    Returns ``{escalation_id, correlation_id, timeline, next_after_id}``;
    ``next_after_id`` is the last event's id (or echoes ``after_id`` when
    no rows landed) so the client can use it as the next cursor.
    """
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT id FROM escalation_request WHERE correlation_id = ?",
        (correlation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    escalation_id = row[0]

    events = await _fetch_timeline(
        conn,
        escalation_request_id=escalation_id,
        after_id=after_id,
        limit=limit,
    )
    next_after_id = events[-1]["id"] if events else after_id
    return {
        "escalation_id": escalation_id,
        "correlation_id": correlation_id,
        "timeline": events,
        "next_after_id": next_after_id,
    }


@router.post("/escalations/{correlation_id}/submit")
async def submit_escalation(
    request: Request,
    correlation_id: str,
) -> dict[str, Any]:
    """Accept the user's submission for a manual-handoff escalation.

    Delegates to :func:`donna.cost.escalation_submit_service.apply_submission`
    so the ``/donna submit`` Discord slash command (slice 20) and the
    claude_code dashboard "Mark as built" modal (slice 21) share the
    exact validation, optimistic lock, and audit-log path. Mode-specific
    affordances all POST the same discriminated-union payload here.
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_json", "message": str(exc)}
        ) from exc

    conn = request.app.state.db.connection
    try:
        result = await apply_submission(
            conn=conn,
            correlation_id=correlation_id,
            payload=payload,
        )
    except SubmissionError as exc:
        detail: dict[str, Any] = {"error": exc.code}
        if exc.message:
            detail["message"] = exc.message
        detail.update(exc.extras)
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc

    return {
        "correlation_id": result.correlation_id,
        "status": result.status,
        "submitted_at": result.submitted_at,
        "iteration": result.iteration,
        "mode": result.mode,
    }


@router.post("/escalations/{correlation_id}/mark-merged")
async def mark_escalation_merged(
    request: Request,
    correlation_id: str,
) -> dict[str, Any]:
    """User clicks **Mark as merged** after merging the validated branch.

    Pure tracking write. Donna does **not** auto-merge (spec §15 — human
    is always the operator for code-writing actions). This endpoint just
    flips ``merged_at`` so the dashboard reflects the user's manual
    merge.

    Returns 409 if the escalation is not in ``status='validated'`` (i.e.
    nothing has actually been validated; merging makes no sense).
    """
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT status, merged_at FROM escalation_request WHERE correlation_id = ?",
        (correlation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    status, merged_at = row[0], row[1]

    if status != "validated":
        raise HTTPException(
            status_code=409,
            detail={"error": "not_validated", "status": status},
        )
    if merged_at is not None:
        # Idempotent — already marked.
        return {"correlation_id": correlation_id, "merged_at": merged_at}

    now_iso = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        UPDATE escalation_request
           SET merged_at = ?
         WHERE correlation_id = ?
           AND status = 'validated'
           AND merged_at IS NULL
        """,
        (now_iso, correlation_id),
    )
    await conn.commit()
    return {"correlation_id": correlation_id, "merged_at": now_iso}
