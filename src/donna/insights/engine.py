"""Insights computation module.

Queries ``invocation_log`` to surface cost centres, system-prompt groups,
quality/cost mismatches, and token-bloat outliers over a configurable
look-back window.

See spec_v3.md for the invocation_log schema and budget reporting
requirements.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

_TOP_COST_CENTERS_SQL = """\
SELECT task_type, SUM(cost_usd) as total_cost, COUNT(*) as call_count,
       ROUND(AVG(tokens_in)) as avg_tokens_in, ROUND(AVG(tokens_out)) as avg_tokens_out
FROM invocation_log
WHERE timestamp >= ? AND is_shadow = 0
GROUP BY task_type
ORDER BY total_cost DESC LIMIT 10
"""

_SYSTEM_PROMPT_GROUPS_SQL = """\
SELECT input_hash, COUNT(*) as call_count, ROUND(AVG(tokens_in)) as avg_tokens_in,
       SUM(cost_usd) as total_cost, MIN(id) as sample_id
FROM invocation_log
WHERE timestamp >= ? AND input_hash != '' AND is_shadow = 0
GROUP BY input_hash HAVING call_count >= 5
ORDER BY total_cost DESC LIMIT 10
"""

_QUALITY_COST_MISMATCHES_SQL = """\
SELECT task_type, ROUND(AVG(cost_usd), 5) as avg_cost,
       ROUND(AVG(quality_score), 3) as avg_quality, COUNT(*) as call_count
FROM invocation_log
WHERE timestamp >= ? AND quality_score IS NOT NULL AND is_shadow = 0
GROUP BY task_type
HAVING avg_cost > (SELECT AVG(cost_usd) FROM invocation_log WHERE timestamp >= ? AND is_shadow = 0)
AND avg_quality < 0.5
"""

_TOKEN_BLOAT_MEDIANS_SQL = """\
SELECT task_type,
       tokens_in AS median_tokens_in
FROM (
    SELECT task_type, tokens_in,
           ROW_NUMBER() OVER (PARTITION BY task_type ORDER BY tokens_in) AS rn,
           COUNT(*) OVER (PARTITION BY task_type) AS cnt
    FROM invocation_log
    WHERE timestamp >= ? AND is_shadow = 0
)
WHERE rn = (cnt + 1) / 2
"""

_TOKEN_BLOAT_OUTLIERS_SQL = """\
SELECT il.id as invocation_id, il.task_type, il.tokens_in,
       m.median_tokens_in,
       ROUND(CAST(il.tokens_in AS REAL) / m.median_tokens_in, 2) as ratio,
       il.cost_usd
FROM invocation_log il
JOIN median_tokens m ON il.task_type = m.task_type
WHERE il.timestamp >= ? AND il.is_shadow = 0
  AND il.tokens_in > 2 * m.median_tokens_in
  AND m.median_tokens_in > 0
ORDER BY il.cost_usd DESC
LIMIT 10
"""


async def compute_insights(
    conn: aiosqlite.Connection,
    payload_dir: Path | None,
    days: int = 7,
) -> dict[str, list[dict[str, Any]]]:
    """Compute cost and quality insights from the invocation log.

    Args:
        conn: An open aiosqlite connection to the task database.
        payload_dir: Optional path to the payload directory (reserved for
            future use inspecting stored prompt payloads).
        days: Look-back window in days.  Defaults to 7.

    Returns:
        A dict with four insight categories: ``top_cost_centers``,
        ``system_prompt_groups``, ``quality_cost_mismatches``, and
        ``token_bloat_outliers``.
    """
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    logger.info(
        "compute_insights.start",
        days=days,
        since=since,
        payload_dir=str(payload_dir) if payload_dir else None,
    )

    top_cost_centers = await _query_top_cost_centers(conn, since)
    system_prompt_groups = await _query_system_prompt_groups(conn, since, days)
    quality_cost_mismatches = await _query_quality_cost_mismatches(conn, since)
    token_bloat_outliers = await _query_token_bloat_outliers(conn, since)

    logger.info(
        "compute_insights.done",
        top_cost_centers=len(top_cost_centers),
        system_prompt_groups=len(system_prompt_groups),
        quality_cost_mismatches=len(quality_cost_mismatches),
        token_bloat_outliers=len(token_bloat_outliers),
    )

    return {
        "top_cost_centers": top_cost_centers,
        "system_prompt_groups": system_prompt_groups,
        "quality_cost_mismatches": quality_cost_mismatches,
        "token_bloat_outliers": token_bloat_outliers,
    }


# ---------------------------------------------------------------------------
# Private query helpers
# ---------------------------------------------------------------------------


async def _query_top_cost_centers(
    conn: aiosqlite.Connection,
    since: str,
) -> list[dict[str, Any]]:
    """Top 10 task types by total cost."""
    cursor = await conn.execute(_TOP_COST_CENTERS_SQL, (since,))
    rows = await cursor.fetchall()
    return [
        {
            "task_type": row[0],
            "total_cost": row[1],
            "call_count": row[2],
            "avg_tokens_in": int(row[3]),
            "avg_tokens_out": int(row[4]),
        }
        for row in rows
    ]


async def _query_system_prompt_groups(
    conn: aiosqlite.Connection,
    since: str,
    days: int,
) -> list[dict[str, Any]]:
    """Top 10 system-prompt groups by cost (deduplicated by input_hash)."""
    cursor = await conn.execute(_SYSTEM_PROMPT_GROUPS_SQL, (since,))
    rows = await cursor.fetchall()
    # Extrapolate observed cost to a weekly estimate.
    weekly_factor = 7.0 / days if days > 0 else 1.0
    return [
        {
            "hash": row[0],
            "call_count": row[1],
            "avg_tokens_in": int(row[2]),
            "estimated_weekly_cost": round(row[3] * weekly_factor, 6),
            "sample_invocation_id": row[4],
        }
        for row in rows
    ]


async def _query_quality_cost_mismatches(
    conn: aiosqlite.Connection,
    since: str,
) -> list[dict[str, Any]]:
    """Task types with above-average cost but below-0.5 quality."""
    cursor = await conn.execute(_QUALITY_COST_MISMATCHES_SQL, (since, since))
    rows = await cursor.fetchall()
    return [
        {
            "task_type": row[0],
            "avg_cost": row[1],
            "avg_quality_score": row[2],
            "call_count": row[3],
        }
        for row in rows
    ]


async def _query_token_bloat_outliers(
    conn: aiosqlite.Connection,
    since: str,
) -> list[dict[str, Any]]:
    """Individual calls where tokens_in exceeds 2x the median for that type."""
    # Step 1: compute medians into a temp table.
    await conn.execute("DROP TABLE IF EXISTS median_tokens")
    await conn.execute(
        f"CREATE TEMP TABLE median_tokens AS {_TOKEN_BLOAT_MEDIANS_SQL}",
        (since,),
    )

    # Step 2: join back to find outliers.
    cursor = await conn.execute(_TOKEN_BLOAT_OUTLIERS_SQL, (since,))
    rows = await cursor.fetchall()

    await conn.execute("DROP TABLE IF EXISTS median_tokens")

    return [
        {
            "invocation_id": row[0],
            "task_type": row[1],
            "tokens_in": row[2],
            "median_for_type": row[3],
            "ratio": row[4],
            "cost_usd": row[5],
        }
        for row in rows
    ]
