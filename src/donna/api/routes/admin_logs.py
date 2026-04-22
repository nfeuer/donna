"""Log viewer endpoints for the Donna Management GUI.

Queries Loki HTTP API for structured logs, with fallback to
invocation_log when Loki is unavailable. Provides event type
hierarchy for tree filtering and correlation trace views.

See docs/observability.md for the full event type taxonomy.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import structlog
from fastapi import Query, Request

from donna.api.auth import admin_router

logger = structlog.get_logger()

router = admin_router()

_LOKI_URL = os.environ.get("DONNA_LOKI_URL", "http://donna-loki:3100")

# Static event type hierarchy matching docs/observability.md
EVENT_TYPE_TREE: dict[str, list[str]] = {
    "task": [
        "created", "state_changed", "dedup_detected",
        "overdue", "escalation_triggered",
    ],
    "api": [
        "call.started", "call.completed", "call.failed",
        "call.retried", "circuit_breaker.opened", "circuit_breaker.closed",
    ],
    "agent": [
        "dispatched", "progress", "completed", "failed",
        "timeout", "interrogation.sent", "interrogation.response_received",
    ],
    "scheduler": [
        "weekly_plan", "daily_recalc", "slot_assigned", "conflict_detected",
    ],
    "notification": [
        "sent", "failed", "escalated", "acknowledged", "blackout_blocked",
    ],
    "preference": [
        "correction_logged", "rule_extracted", "rule_applied", "rule_disabled",
    ],
    "system": [
        "startup", "shutdown", "health_check",
        "backup.completed", "backup.failed", "migration.applied",
    ],
    "cost": [
        "daily_threshold", "monthly_warning", "agent_paused", "budget_increase",
    ],
    "sync": [
        "supabase.push", "supabase.failed", "keepalive.sent",
    ],
    "admin": [
        "request", "config.saved", "config.read", "prompt.saved",
    ],
    "llm_gateway": [
        "enqueued", "dequeued", "interrupted", "completed",
        "rejected", "drain_started", "config_reloaded", "alert",
    ],
    "ui": [
        "request",
    ],
}


@router.get("/logs/event-types")
async def get_event_types() -> dict[str, list[str]]:
    """Return the static event type hierarchy for the tree filter."""
    return EVENT_TYPE_TREE


@router.get("/logs/trace/{correlation_id}")
async def get_trace(
    request: Request,
    correlation_id: str,
) -> dict[str, Any]:
    """Fetch all log entries for a correlation ID (trace timeline).

    Tries Loki first, falls back to invocation_log.
    """
    # Try Loki
    try:
        entries = await _query_loki_trace(correlation_id)
        if entries:
            return {
                "correlation_id": correlation_id,
                "entries": entries,
                "source": "loki",
                "count": len(entries),
            }
    except Exception as exc:
        logger.debug("loki_trace_fallback", correlation_id=correlation_id, error=str(exc))

    # Fallback: search invocation_log (limited — only has LLM calls)
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        """SELECT id, timestamp, task_type, model_alias, latency_ms,
                  tokens_in, tokens_out, cost_usd, task_id
           FROM invocation_log
           WHERE task_id IN (
               SELECT DISTINCT task_id FROM invocation_log
               WHERE id = ? OR task_id = ?
           )
           ORDER BY timestamp ASC""",
        (correlation_id, correlation_id),
    )
    rows = await cursor.fetchall()
    entries = [
        {
            "timestamp": row[1],
            "event_type": "api.call.completed",
            "level": "INFO",
            "service": "model_router",
            "message": f"{row[2]} via {row[3]} ({row[4]}ms)",
            "task_id": row[8],
            "correlation_id": correlation_id,
            "extra": {
                "invocation_id": row[0],
                "task_type": row[2],
                "model": row[3],
                "latency_ms": row[4],
                "tokens_in": row[5],
                "tokens_out": row[6],
                "cost_usd": float(row[7]),
            },
        }
        for row in rows
    ]

    return {
        "correlation_id": correlation_id,
        "entries": entries,
        "source": "invocation_log_fallback",
        "count": len(entries),
    }


@router.get("/logs")
async def get_logs(
    request: Request,
    event_type: str | None = Query(default=None, description="Comma-separated event types"),
    level: str | None = Query(default=None, description="Comma-separated levels"),
    service: str | None = Query(default=None),
    search: str | None = Query(default=None, description="Full-text search"),
    correlation_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    start: str | None = Query(default=None, description="ISO timestamp"),
    end: str | None = Query(default=None, description="ISO timestamp"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated log query with filtering.

    Queries Loki HTTP API, falling back to invocation_log for partial
    coverage when Loki is unavailable.
    """
    # Try Loki first
    try:
        result = await _query_loki(
            event_type=event_type,
            level=level,
            service=service,
            search=search,
            correlation_id=correlation_id,
            task_id=task_id,
            start=start,
            end=end,
            limit=limit,
        )
        if result is not None:
            # Apply offset for pagination (Loki doesn't support offset natively)
            entries = result[offset:offset + limit] if offset > 0 else result[:limit]
            return {
                "entries": entries,
                "total": len(result),
                "limit": limit,
                "offset": offset,
                "source": "loki",
            }
    except Exception as exc:
        logger.debug("loki_query_fallback", error=str(exc))

    # Fallback: query invocation_log
    return await _query_invocation_log_fallback(
        request, event_type, level, service, search,
        task_id, start, end, limit, offset,
    )


async def _query_loki(
    *,
    event_type: str | None,
    level: str | None,
    service: str | None,
    search: str | None,
    correlation_id: str | None,
    task_id: str | None,
    start: str | None,
    end: str | None,
    limit: int,
) -> list[dict[str, Any]] | None:
    """Build and execute a LogQL query against Loki."""
    # Build stream selector
    selectors: list[str] = []
    if service:
        selectors.append(f'service="{service}"')

    stream = "{" + ", ".join(selectors) + "}" if selectors else '{service=~".+"}'

    # Build pipeline
    pipeline_parts: list[str] = ["json"]

    if search:
        # Line filter before JSON parsing for efficiency
        stream_with_filter = f'{stream} |= `{search}`'
        stream = stream_with_filter

    if level:
        levels = "|".join(lvl.strip() for lvl in level.split(","))
        pipeline_parts.append(f'level=~"{levels}"')

    if event_type:
        types = "|".join(t.strip().replace(".", "\\.") for t in event_type.split(","))
        pipeline_parts.append(f'event_type=~"{types}"')

    if correlation_id:
        pipeline_parts.append(f'correlation_id="{correlation_id}"')

    if task_id:
        pipeline_parts.append(f'task_id="{task_id}"')

    query = stream + " | " + " | ".join(pipeline_parts)

    # Time range
    now = datetime.now(UTC)
    start_ts = start or (now - timedelta(hours=24)).isoformat()
    end_ts = end or now.isoformat()

    params = {
        "query": query,
        "start": start_ts,
        "end": end_ts,
        "limit": str(min(limit * 3, 1500)),  # Over-fetch for offset support
        "direction": "backward",
    }

    async with aiohttp.ClientSession() as session, session.get(
        f"{_LOKI_URL}/loki/api/v1/query_range",
        params=params,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status != 200:
            logger.warning("loki_query_failed", status=resp.status)
            return None
        data = await resp.json()

    entries: list[dict[str, Any]] = []
    for stream_result in data.get("data", {}).get("result", []):
        stream_labels = stream_result.get("stream", {})
        for ts, line in stream_result.get("values", []):
            try:
                parsed = json.loads(line)
            except (ValueError, TypeError):
                parsed = {"message": line}

            entries.append({
                "timestamp": parsed.get("timestamp", ts),
                "level": parsed.get("level", stream_labels.get("level", "INFO")),
                "event_type": parsed.get("event_type", ""),
                "message": parsed.get("message", parsed.get("event", "")),
                "service": parsed.get("service", stream_labels.get("service", "")),
                "component": parsed.get("component", ""),
                "correlation_id": parsed.get("correlation_id", ""),
                "task_id": parsed.get("task_id", ""),
                "user_id": parsed.get("user_id", ""),
                "agent_id": parsed.get("agent_id", ""),
                "duration_ms": parsed.get("duration_ms"),
                "cost_usd": parsed.get("cost_usd"),
                "extra": {
                    k: v for k, v in parsed.items()
                    if k not in {
                        "timestamp", "level", "event_type", "message",
                        "service", "component", "correlation_id", "task_id",
                        "user_id", "agent_id", "duration_ms", "cost_usd",
                    }
                },
            })

    return entries


async def _query_loki_trace(correlation_id: str) -> list[dict[str, Any]]:
    """Query Loki for all events with a specific correlation_id."""
    result = await _query_loki(
        event_type=None,
        level=None,
        service=None,
        search=None,
        correlation_id=correlation_id,
        task_id=None,
        start=None,
        end=None,
        limit=200,
    )
    if result:
        result.sort(key=lambda e: e.get("timestamp", ""))
    return result or []


async def _query_invocation_log_fallback(
    request: Request,
    event_type: str | None,
    level: str | None,
    service: str | None,
    search: str | None,
    task_id: str | None,
    start: str | None,
    end: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Fallback: query invocation_log when Loki is unavailable."""
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if event_type:
        types = [t.strip() for t in event_type.split(",")]
        # Map event types to task_types where possible
        task_types = [t.replace("api.call.", "").replace("agent.", "") for t in types]
        placeholders = ", ".join("?" for _ in task_types)
        where_clauses.append(f"task_type IN ({placeholders})")
        params.extend(task_types)

    if task_id:
        where_clauses.append("task_id = ?")
        params.append(task_id)

    if search:
        where_clauses.append("(task_type LIKE ? OR model_alias LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    if start:
        where_clauses.append("timestamp >= ?")
        params.append(start)

    if end:
        where_clauses.append("timestamp <= ?")
        params.append(end)

    # Safe: {where} is built from static column names; user values go through params
    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Count total
    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM invocation_log WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    # Fetch page
    cursor = await conn.execute(
        f"""SELECT id, timestamp, task_type, model_alias, latency_ms,
                   tokens_in, tokens_out, cost_usd, task_id, quality_score, is_shadow
            FROM invocation_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    )
    rows = await cursor.fetchall()

    entries = [
        {
            "timestamp": row[1],
            "level": "INFO",
            "event_type": "api.call.completed",
            "message": f"{row[2]} via {row[3]} ({row[4]}ms, ${row[7]:.4f})",
            "service": "model_router",
            "component": row[2],
            "correlation_id": "",
            "task_id": row[8] or "",
            "user_id": "",
            "extra": {
                "invocation_id": row[0],
                "task_type": row[2],
                "model": row[3],
                "latency_ms": row[4],
                "tokens_in": row[5],
                "tokens_out": row[6],
                "cost_usd": float(row[7]),
                "quality_score": float(row[9]) if row[9] is not None else None,
                "is_shadow": bool(row[10]),
            },
        }
        for row in rows
    ]

    return {
        "entries": entries,
        "total": total,
        "limit": limit,
        "offset": offset,
        "source": "invocation_log_fallback",
    }
