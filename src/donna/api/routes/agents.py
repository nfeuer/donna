"""Agent activity and cost endpoints — powers the Flutter cost dashboard.

Reads directly from invocation_log via the aiosqlite connection.
All queries are scoped to the authenticated user_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from donna.api.auth import CurrentUser

router = APIRouter()

# Hard-coded budget limits; these mirror the values in docs/model-layer.md.
# The orchestrator enforces these via the BudgetGuard; the API surfaces them
# to the Flutter cost dashboard.
_DAILY_BUDGET_USD = 20.0
_MONTHLY_BUDGET_USD = 100.0


@router.get("/activity")
async def get_agent_activity(
    request: Request,
    user_id: CurrentUser,
    limit: int = 20,
) -> dict[str, Any]:
    """Return recent LLM invocations for the authenticated user.

    Results include task_type, model, token counts, cost, and latency.
    Sorted by timestamp descending (most recent first).
    """
    db = request.app.state.db
    conn = db.connection

    cursor = await conn.execute(
        """
        SELECT id, task_type, model_alias, model_actual,
               latency_ms, tokens_in, tokens_out, cost_usd,
               timestamp, task_id
        FROM invocation_log
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (user_id, max(1, min(limit, 500))),
    )
    rows = await cursor.fetchall()

    activity = [
        {
            "id": row[0],
            "task_type": row[1],
            "model_alias": row[2],
            "model": row[3],
            "latency_ms": row[4],
            "tokens_in": row[5],
            "tokens_out": row[6],
            "cost_usd": row[7],
            "timestamp": row[8],
            "task_id": row[9],
        }
        for row in rows
    ]

    return {"user_id": user_id, "activity": activity, "count": len(activity)}


@router.get("/cost")
async def get_cost_summary(
    request: Request,
    user_id: CurrentUser,
) -> dict[str, Any]:
    """Return daily and monthly cost totals for the authenticated user.

    Budgets are read from constants defined in docs/model-layer.md:
      - Daily pause threshold: $20
      - Monthly hard cap: $100
    """
    db = request.app.state.db
    conn = db.connection

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    cursor = await conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN timestamp >= :day  THEN cost_usd ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN timestamp >= :mon  THEN cost_usd ELSE 0 END), 0),
            COUNT(CASE WHEN timestamp >= :day  THEN 1 END),
            COUNT(CASE WHEN timestamp >= :mon  THEN 1 END)
        FROM invocation_log
        WHERE user_id = :uid
        """,
        {"day": day_start, "mon": month_start, "uid": user_id},
    )
    row = await cursor.fetchone()

    daily_cost = float(row[0]) if row else 0.0
    monthly_cost = float(row[1]) if row else 0.0
    daily_calls = int(row[2]) if row else 0
    monthly_calls = int(row[3]) if row else 0

    return {
        "user_id": user_id,
        "daily": {
            "cost_usd": round(daily_cost, 4),
            "calls": daily_calls,
            "budget_usd": _DAILY_BUDGET_USD,
            "budget_remaining_usd": round(max(0.0, _DAILY_BUDGET_USD - daily_cost), 4),
            "paused": daily_cost >= _DAILY_BUDGET_USD,
        },
        "monthly": {
            "cost_usd": round(monthly_cost, 4),
            "calls": monthly_calls,
            "budget_usd": _MONTHLY_BUDGET_USD,
            "budget_remaining_usd": round(max(0.0, _MONTHLY_BUDGET_USD - monthly_cost), 4),
            "over_cap": monthly_cost >= _MONTHLY_BUDGET_USD,
        },
        "timestamp": now.isoformat(),
    }
