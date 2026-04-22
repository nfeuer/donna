"""Shadow scoring endpoints for the Donna Management GUI.

Compare primary model outputs against shadow model runs, view quality
score comparisons, and manage the spot-check review queue.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Query, Request

from donna.api.auth import admin_router

router = admin_router()


def _invocation_dict(row: tuple[Any, ...], columns: list[str]) -> dict[str, Any]:
    """Convert a row tuple to a dict using column names."""
    d: dict[str, Any] = {}
    for i, col in enumerate(columns):
        val = row[i]
        if col == "cost_usd":
            val = float(val) if val is not None else 0.0
        elif col == "quality_score":
            val = float(val) if val is not None else None
        elif col in ("is_shadow", "spot_check_queued"):
            val = bool(val)
        elif col == "output":
            if val:
                try:
                    val = json.loads(val) if isinstance(val, str) else val
                except (ValueError, TypeError):
                    val = {"raw": str(val)}
            else:
                val = None
        d[col] = val
    return d


_COMPARISON_COLS = [
    "id", "timestamp", "task_type", "task_id", "model_alias", "model_actual",
    "input_hash", "latency_ms", "tokens_in", "tokens_out", "cost_usd",
    "output", "quality_score", "is_shadow", "spot_check_queued", "user_id",
]

_SUMMARY_COLS = [
    "id", "timestamp", "task_type", "task_id", "model_alias", "model_actual",
    "latency_ms", "tokens_in", "tokens_out", "cost_usd",
    "quality_score", "is_shadow", "spot_check_queued", "user_id",
]


@router.get("/shadow/comparisons")
async def list_shadow_comparisons(
    request: Request,
    task_type: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Pair primary and shadow invocations by input_hash or task_id proximity."""
    conn = request.app.state.db.connection
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    where_extra = ""
    params: list[Any] = [since, since]
    if task_type:
        where_extra = " AND p.task_type = ?"
        params.append(task_type)

    ", ".join(_COMPARISON_COLS)
    p_cols = ", ".join(f"p.{c}" for c in _COMPARISON_COLS)
    s_cols = ", ".join(f"s.{c}" for c in _COMPARISON_COLS)

    # Safe: {p_cols}, {s_cols}, {where_extra} are built from static column names
    # Match by input_hash (primary + shadow share the same prompt hash)
    query = f"""
        SELECT {p_cols}, {s_cols}
        FROM invocation_log p
        INNER JOIN invocation_log s
            ON p.input_hash = s.input_hash
            AND p.is_shadow = 0 AND s.is_shadow = 1
        WHERE p.timestamp >= ? AND s.timestamp >= ?
        {where_extra}
        ORDER BY p.timestamp DESC
        LIMIT ?
    """
    params.append(limit)

    cursor = await conn.execute(query, params)
    rows = await cursor.fetchall()

    n = len(_COMPARISON_COLS)
    comparisons = []
    for row in rows:
        primary = _invocation_dict(row[:n], _COMPARISON_COLS)
        shadow = _invocation_dict(row[n:], _COMPARISON_COLS)
        p_q = primary.get("quality_score")
        s_q = shadow.get("quality_score")
        delta = (s_q - p_q) if (s_q is not None and p_q is not None) else None
        comparisons.append({
            "primary": primary,
            "shadow": shadow,
            "quality_delta": round(delta, 4) if delta is not None else None,
        })

    # If we got fewer than limit by input_hash, also try task_id proximity
    if len(comparisons) < limit:
        existing_shadow_ids = {
            c["shadow"]["id"] for c in comparisons
            if isinstance(c["shadow"], dict)
        }
        remaining = limit - len(comparisons)

        prox_where = ""
        prox_params: list[Any] = [since, since]
        if task_type:
            prox_where = " AND p.task_type = ?"
            prox_params.append(task_type)

        prox_query = f"""
            SELECT {p_cols}, {s_cols}
            FROM invocation_log p
            INNER JOIN invocation_log s
                ON p.task_id = s.task_id
                AND p.task_id IS NOT NULL
                AND p.is_shadow = 0 AND s.is_shadow = 1
                AND ABS(JULIANDAY(p.timestamp) - JULIANDAY(s.timestamp)) < 0.0007
            WHERE p.timestamp >= ? AND s.timestamp >= ?
            AND p.input_hash != s.input_hash
            {prox_where}
            ORDER BY p.timestamp DESC
            LIMIT ?
        """
        prox_params.append(remaining)
        cursor = await conn.execute(prox_query, prox_params)
        prox_rows = await cursor.fetchall()

        for row in prox_rows:
            shadow = _invocation_dict(row[n:], _COMPARISON_COLS)
            if shadow["id"] in existing_shadow_ids:
                continue
            primary = _invocation_dict(row[:n], _COMPARISON_COLS)
            p_q = primary.get("quality_score")
            s_q = shadow.get("quality_score")
            delta = (s_q - p_q) if (s_q is not None and p_q is not None) else None
            comparisons.append({
                "primary": primary,
                "shadow": shadow,
                "quality_delta": round(delta, 4) if delta is not None else None,
            })

    return {
        "comparisons": comparisons,
        "total": len(comparisons),
    }


@router.get("/shadow/stats")
async def shadow_stats(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Aggregate shadow vs primary quality and cost stats."""
    conn = request.app.state.db.connection
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    # Average quality scores
    cursor = await conn.execute(
        """SELECT
               AVG(CASE WHEN is_shadow = 0 THEN quality_score END) AS primary_avg,
               AVG(CASE WHEN is_shadow = 1 THEN quality_score END) AS shadow_avg,
               SUM(CASE WHEN is_shadow = 0 THEN cost_usd ELSE 0 END) AS primary_cost,
               SUM(CASE WHEN is_shadow = 1 THEN cost_usd ELSE 0 END) AS shadow_cost,
               COUNT(CASE WHEN is_shadow = 0 THEN 1 END) AS primary_count,
               COUNT(CASE WHEN is_shadow = 1 THEN 1 END) AS shadow_count
           FROM invocation_log
           WHERE timestamp >= ? AND quality_score IS NOT NULL""",
        (since,),
    )
    row = await cursor.fetchone()
    primary_avg = float(row[0]) if row[0] is not None else None
    shadow_avg = float(row[1]) if row[1] is not None else None
    primary_cost = float(row[2]) if row[2] else 0.0
    shadow_cost = float(row[3]) if row[3] else 0.0

    avg_delta = None
    if primary_avg is not None and shadow_avg is not None:
        avg_delta = round(shadow_avg - primary_avg, 4)

    # Win/loss/tie from paired comparisons
    cursor = await conn.execute(
        """SELECT
               SUM(CASE WHEN s.quality_score > p.quality_score THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN s.quality_score < p.quality_score THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN s.quality_score = p.quality_score THEN 1 ELSE 0 END) AS ties
           FROM invocation_log p
           INNER JOIN invocation_log s
               ON p.input_hash = s.input_hash
               AND p.is_shadow = 0 AND s.is_shadow = 1
           WHERE p.timestamp >= ?
               AND p.quality_score IS NOT NULL
               AND s.quality_score IS NOT NULL""",
        (since,),
    )
    wlt = await cursor.fetchone()
    wins = int(wlt[0]) if wlt[0] else 0
    losses = int(wlt[1]) if wlt[1] else 0
    ties = int(wlt[2]) if wlt[2] else 0

    # Daily shadow quality trend
    cursor = await conn.execute(
        """SELECT DATE(timestamp) AS day,
                  AVG(quality_score) AS avg_quality,
                  COUNT(*) AS count
           FROM invocation_log
           WHERE is_shadow = 1 AND timestamp >= ? AND quality_score IS NOT NULL
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (since,),
    )
    trend_rows = await cursor.fetchall()
    trend = [
        {"date": r[0], "avg_quality": round(float(r[1]), 4), "count": int(r[2])}
        for r in trend_rows
    ]

    return {
        "primary_avg_quality": round(primary_avg, 4) if primary_avg is not None else None,
        "shadow_avg_quality": round(shadow_avg, 4) if shadow_avg is not None else None,
        "avg_delta": avg_delta,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "primary_cost": round(primary_cost, 4),
        "shadow_cost": round(shadow_cost, 4),
        "primary_count": int(row[4]) if row[4] else 0,
        "shadow_count": int(row[5]) if row[5] else 0,
        "trend": trend,
        "days": days,
    }


@router.get("/shadow/spot-checks")
async def list_spot_checks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Invocations flagged for manual review."""
    conn = request.app.state.db.connection

    # Safe: {where} is a static string with no user input
    where = "(spot_check_queued = 1) OR (quality_score IS NOT NULL AND quality_score < 0.7)"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM invocation_log WHERE {where}"
    )
    total = (await cursor.fetchone())[0]

    col_list = ", ".join(_SUMMARY_COLS)
    cursor = await conn.execute(
        f"""SELECT {col_list}
            FROM invocation_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        (limit, offset),
    )
    rows = await cursor.fetchall()

    items = [_invocation_dict(row, _SUMMARY_COLS) for row in rows]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
