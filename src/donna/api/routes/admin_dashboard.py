"""Dashboard KPI endpoints for the Donna Management GUI.

Provides aggregated metrics for:
- Parse accuracy (from correction_log vs invocation_log)
- Agent performance (from invocation_log)
- Task throughput (from tasks table)
- Cost analytics (from invocation_log via CostTracker)
- Quality warnings (low quality_score invocations)

All endpoints are unauthenticated — admin-only, local dev tool.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Query, Request

router = APIRouter()


def _days_ago(days: int) -> str:
    """Return ISO timestamp for N days ago at midnight UTC."""
    dt = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=days)
    return dt.isoformat()


@router.get("/dashboard/parse-accuracy")
async def get_parse_accuracy(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Parse accuracy rate over time.

    Compares total parse_task invocations against corrections logged
    in correction_log. Breaks down by field_corrected.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)

    # Total parses per day
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day, COUNT(*) as count
           FROM invocation_log
           WHERE task_type = 'parse_task' AND timestamp >= ?
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (since,),
    )
    daily_parses = {row[0]: row[1] for row in await cursor.fetchall()}

    # Corrections per day
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day, COUNT(*) as count
           FROM correction_log
           WHERE timestamp >= ?
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (since,),
    )
    daily_corrections = {row[0]: row[1] for row in await cursor.fetchall()}

    # Build daily time series
    all_days = sorted(set(list(daily_parses.keys()) + list(daily_corrections.keys())))
    time_series = []
    for day in all_days:
        parses = daily_parses.get(day, 0)
        corrections = daily_corrections.get(day, 0)
        accuracy = max(0.0, (parses - corrections) / parses * 100) if parses > 0 else 100.0
        time_series.append({
            "date": day,
            "parses": parses,
            "corrections": corrections,
            "accuracy": round(accuracy, 1),
        })

    # Totals
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM invocation_log WHERE task_type = 'parse_task' AND timestamp >= ?",
        (since,),
    )
    total_parses = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM correction_log WHERE timestamp >= ?",
        (since,),
    )
    total_corrections = (await cursor.fetchone())[0]

    overall_accuracy = (
        max(0.0, (total_parses - total_corrections) / total_parses * 100)
        if total_parses > 0
        else 100.0
    )

    # Breakdown by field_corrected
    cursor = await conn.execute(
        """SELECT field_corrected, COUNT(*) as count
           FROM correction_log
           WHERE timestamp >= ?
           GROUP BY field_corrected
           ORDER BY count DESC""",
        (since,),
    )
    field_breakdown = [
        {"field": row[0], "count": row[1]} for row in await cursor.fetchall()
    ]

    return {
        "summary": {
            "total_parses": total_parses,
            "total_corrections": total_corrections,
            "accuracy_pct": round(overall_accuracy, 1),
            "most_corrected_field": field_breakdown[0]["field"] if field_breakdown else None,
        },
        "time_series": time_series,
        "field_breakdown": field_breakdown,
        "days": days,
    }


@router.get("/dashboard/agent-performance")
async def get_agent_performance(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Agent performance metrics from invocation_log.

    Groups by task_type (which maps to agent roles) and aggregates
    call counts, latency, tokens, cost, and quality scores.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)

    # Per-task-type aggregation
    cursor = await conn.execute(
        """SELECT
               task_type,
               COUNT(*) as call_count,
               ROUND(AVG(latency_ms), 0) as avg_latency_ms,
               MAX(latency_ms) as max_latency_ms,
               SUM(tokens_in) as total_tokens_in,
               SUM(tokens_out) as total_tokens_out,
               ROUND(SUM(cost_usd), 4) as total_cost_usd,
               ROUND(AVG(cost_usd), 6) as avg_cost_usd,
               COUNT(CASE WHEN quality_score IS NOT NULL THEN 1 END) as scored_count,
               ROUND(AVG(quality_score), 2) as avg_quality_score
           FROM invocation_log
           WHERE timestamp >= ? AND is_shadow = 0
           GROUP BY task_type
           ORDER BY call_count DESC""",
        (since,),
    )
    rows = await cursor.fetchall()
    agents = [
        {
            "task_type": row[0],
            "call_count": row[1],
            "avg_latency_ms": int(row[2] or 0),
            "max_latency_ms": row[3],
            "total_tokens_in": row[4],
            "total_tokens_out": row[5],
            "total_cost_usd": float(row[6] or 0),
            "avg_cost_usd": float(row[7] or 0),
            "scored_count": row[8],
            "avg_quality_score": float(row[9]) if row[9] is not None else None,
        }
        for row in rows
    ]

    # Daily time series (calls per day per task_type)
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day, task_type, COUNT(*) as count
           FROM invocation_log
           WHERE timestamp >= ? AND is_shadow = 0
           GROUP BY day, task_type
           ORDER BY day""",
        (since,),
    )
    daily_rows = await cursor.fetchall()
    daily_series: dict[str, list[dict[str, Any]]] = {}
    for row in daily_rows:
        day, task_type, count = row[0], row[1], row[2]
        if day not in daily_series:
            daily_series[day] = []
        daily_series[day].append({"task_type": task_type, "count": count})

    time_series = [
        {"date": day, "breakdown": entries}
        for day, entries in sorted(daily_series.items())
    ]

    # Overall summary
    total_calls = sum(a["call_count"] for a in agents)
    total_cost = sum(a["total_cost_usd"] for a in agents)
    avg_latency = (
        sum(a["avg_latency_ms"] * a["call_count"] for a in agents) / total_calls
        if total_calls > 0
        else 0
    )

    # Latency percentile (p95 approximation via sorted values)
    cursor = await conn.execute(
        """SELECT latency_ms FROM invocation_log
           WHERE timestamp >= ? AND is_shadow = 0
           ORDER BY latency_ms""",
        (since,),
    )
    all_latencies = [row[0] for row in await cursor.fetchall()]
    p95_latency = (
        all_latencies[int(len(all_latencies) * 0.95)] if all_latencies else 0
    )

    return {
        "summary": {
            "total_calls": total_calls,
            "avg_latency_ms": int(avg_latency),
            "p95_latency_ms": p95_latency,
            "total_cost_usd": round(total_cost, 4),
        },
        "agents": agents,
        "time_series": time_series,
        "days": days,
    }


@router.get("/dashboard/task-throughput")
async def get_task_throughput(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Task lifecycle metrics from the tasks table.

    Tracks creation vs completion rates, status distribution,
    reschedule frequency, and overdue counts.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)
    now = datetime.now(timezone.utc).isoformat()

    # Created per day
    cursor = await conn.execute(
        """SELECT DATE(created_at) as day, COUNT(*) as count
           FROM tasks WHERE created_at >= ?
           GROUP BY DATE(created_at) ORDER BY day""",
        (since,),
    )
    daily_created = {row[0]: row[1] for row in await cursor.fetchall()}

    # Completed per day
    cursor = await conn.execute(
        """SELECT DATE(completed_at) as day, COUNT(*) as count
           FROM tasks WHERE completed_at >= ? AND completed_at IS NOT NULL
           GROUP BY DATE(completed_at) ORDER BY day""",
        (since,),
    )
    daily_completed = {row[0]: row[1] for row in await cursor.fetchall()}

    all_days = sorted(set(list(daily_created.keys()) + list(daily_completed.keys())))
    time_series = [
        {
            "date": day,
            "created": daily_created.get(day, 0),
            "completed": daily_completed.get(day, 0),
        }
        for day in all_days
    ]

    # Status distribution (current snapshot)
    cursor = await conn.execute(
        "SELECT status, COUNT(*) FROM tasks GROUP BY status",
    )
    status_distribution = {row[0]: row[1] for row in await cursor.fetchall()}

    # Totals in period
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE created_at >= ?", (since,)
    )
    total_created = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE completed_at >= ? AND completed_at IS NOT NULL",
        (since,),
    )
    total_completed = (await cursor.fetchone())[0]

    # Overdue count
    cursor = await conn.execute(
        """SELECT COUNT(*) FROM tasks
           WHERE deadline IS NOT NULL AND deadline < ?
           AND status NOT IN ('done', 'cancelled')""",
        (now,),
    )
    overdue_count = (await cursor.fetchone())[0]

    # Avg reschedule count for active tasks
    cursor = await conn.execute(
        """SELECT ROUND(AVG(reschedule_count), 1) FROM tasks
           WHERE status NOT IN ('done', 'cancelled') AND reschedule_count > 0""",
    )
    avg_reschedules = (await cursor.fetchone())[0] or 0.0

    # Avg completion time (hours)
    cursor = await conn.execute(
        """SELECT ROUND(AVG(
               (julianday(completed_at) - julianday(created_at)) * 24
           ), 1) FROM tasks
           WHERE completed_at IS NOT NULL AND completed_at >= ?""",
        (since,),
    )
    avg_completion_hours = (await cursor.fetchone())[0]

    # Domain breakdown
    cursor = await conn.execute(
        """SELECT domain, COUNT(*) as total,
               COUNT(CASE WHEN completed_at IS NOT NULL AND completed_at >= ? THEN 1 END) as completed
           FROM tasks WHERE created_at >= ?
           GROUP BY domain""",
        (since, since),
    )
    domain_breakdown = [
        {"domain": row[0], "total": row[1], "completed": row[2]}
        for row in await cursor.fetchall()
    ]

    return {
        "summary": {
            "total_created": total_created,
            "total_completed": total_completed,
            "completion_rate": round(total_completed / total_created * 100, 1) if total_created > 0 else 0,
            "overdue_count": overdue_count,
            "avg_reschedules": float(avg_reschedules),
            "avg_completion_hours": float(avg_completion_hours) if avg_completion_hours else None,
        },
        "status_distribution": status_distribution,
        "time_series": time_series,
        "domain_breakdown": domain_breakdown,
        "days": days,
    }


@router.get("/dashboard/cost-analytics")
async def get_cost_analytics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Cost analytics from invocation_log.

    Provides daily/monthly totals, breakdowns by task_type and model,
    projected monthly spend, and budget utilization.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    config = _load_dashboard_config(request.app.state.config_dir)
    daily_budget = config["daily_budget_usd"]
    monthly_budget = config["monthly_budget_usd"]

    # Daily cost time series
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day,
               ROUND(SUM(cost_usd), 4) as cost,
               COUNT(*) as calls
           FROM invocation_log
           WHERE timestamp >= ?
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (since,),
    )
    time_series = [
        {"date": row[0], "cost_usd": float(row[1]), "calls": row[2]}
        for row in await cursor.fetchall()
    ]

    # Today's cost
    cursor = await conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM invocation_log WHERE timestamp >= ?",
        (today_start,),
    )
    row = await cursor.fetchone()
    today_cost = float(row[0])
    today_calls = row[1]

    # Month-to-date cost
    cursor = await conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM invocation_log WHERE timestamp >= ?",
        (month_start,),
    )
    row = await cursor.fetchone()
    monthly_cost = float(row[0])
    monthly_calls = row[1]

    # 7-day rolling average for projection
    seven_days_ago = _days_ago(7)
    cursor = await conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
        (seven_days_ago,),
    )
    week_cost = float((await cursor.fetchone())[0])
    daily_avg = week_cost / 7.0

    _, days_in_month = calendar.monthrange(now.year, now.month)
    projected_monthly = daily_avg * days_in_month

    # Cost by task_type
    cursor = await conn.execute(
        """SELECT task_type, ROUND(SUM(cost_usd), 4) as cost, COUNT(*) as calls
           FROM invocation_log WHERE timestamp >= ?
           GROUP BY task_type ORDER BY cost DESC""",
        (since,),
    )
    by_task_type = [
        {"task_type": row[0], "cost_usd": float(row[1]), "calls": row[2]}
        for row in await cursor.fetchall()
    ]

    # Cost by model
    cursor = await conn.execute(
        """SELECT model_alias, ROUND(SUM(cost_usd), 4) as cost, COUNT(*) as calls
           FROM invocation_log WHERE timestamp >= ?
           GROUP BY model_alias ORDER BY cost DESC""",
        (since,),
    )
    by_model = [
        {"model": row[0], "cost_usd": float(row[1]), "calls": row[2]}
        for row in await cursor.fetchall()
    ]

    return {
        "summary": {
            "today_cost_usd": round(today_cost, 4),
            "today_calls": today_calls,
            "monthly_cost_usd": round(monthly_cost, 4),
            "monthly_calls": monthly_calls,
            "projected_monthly_usd": round(projected_monthly, 2),
            "daily_budget_usd": daily_budget,
            "monthly_budget_usd": monthly_budget,
            "daily_utilization_pct": round(today_cost / daily_budget * 100, 1),
            "monthly_utilization_pct": round(monthly_cost / monthly_budget * 100, 1),
            "daily_remaining_usd": round(max(0, daily_budget - today_cost), 4),
            "monthly_remaining_usd": round(max(0, monthly_budget - monthly_cost), 4),
        },
        "time_series": time_series,
        "by_task_type": by_task_type,
        "by_model": by_model,
        "days": days,
    }


@router.get("/dashboard/llm-gateway")
async def get_llm_gateway_analytics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """LLM gateway metrics from invocation_log.

    Aggregates internal vs external calls, interruptions, and
    per-caller breakdowns using the caller and interrupted columns
    added by the gateway queue migration.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)

    # Daily time-series: internal/external/interrupted counts
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day,
               COUNT(CASE WHEN caller IS NULL AND task_type != 'external_llm_call' THEN 1 END) as internal,
               COUNT(CASE WHEN caller IS NOT NULL OR task_type = 'external_llm_call' THEN 1 END) as external,
               COUNT(CASE WHEN interrupted = 1 THEN 1 END) as interrupted,
               ROUND(AVG(latency_ms), 0) as avg_latency_ms
           FROM invocation_log
           WHERE timestamp >= ?
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (since,),
    )
    daily_rows = await cursor.fetchall()

    time_series = [
        {
            "date": row[0],
            "internal": row[1],
            "external": row[2],
            "interrupted": row[3],
            "avg_latency_ms": int(row[4] or 0),
        }
        for row in daily_rows
    ]

    total_internal = sum(r[1] for r in daily_rows)
    total_external = sum(r[2] for r in daily_rows)
    total_interrupted = sum(r[3] for r in daily_rows)
    total_calls = total_internal + total_external
    avg_latency = (
        sum(r[4] * (r[1] + r[2]) for r in daily_rows if r[4]) / total_calls
        if total_calls > 0
        else 0
    )

    # Per-caller breakdown
    cursor = await conn.execute(
        """SELECT
               COALESCE(caller, '_internal') as caller_name,
               COUNT(*) as call_count,
               ROUND(AVG(latency_ms), 0) as avg_latency_ms,
               SUM(tokens_in) as total_tokens_in,
               SUM(tokens_out) as total_tokens_out,
               COUNT(CASE WHEN interrupted = 1 THEN 1 END) as interrupted_count,
               0 as rejected_count
           FROM invocation_log
           WHERE timestamp >= ?
               AND (caller IS NOT NULL OR task_type = 'external_llm_call')
           GROUP BY caller_name
           ORDER BY call_count DESC""",
        (since,),
    )
    caller_rows = await cursor.fetchall()

    by_caller = [
        {
            "caller": row[0],
            "call_count": row[1],
            "avg_latency_ms": int(row[2] or 0),
            "total_tokens_in": row[3] or 0,
            "total_tokens_out": row[4] or 0,
            "interrupted_count": row[5],
            "rejected_count": row[6],
        }
        for row in caller_rows
    ]

    unique_callers = len([c for c in by_caller if c["caller"] != "_internal"])

    return {
        "summary": {
            "total_calls": total_calls,
            "internal_calls": total_internal,
            "external_calls": total_external,
            "total_interrupted": total_interrupted,
            "avg_latency_ms": int(avg_latency),
            "unique_callers": unique_callers,
        },
        "time_series": time_series,
        "by_caller": by_caller,
        "days": days,
    }


def _load_dashboard_config(config_dir: str) -> dict[str, Any]:
    """Load quality_score thresholds and budget from config/dashboard.yaml."""
    path = Path(config_dir) / "dashboard.yaml"
    defaults = {
        "critical_threshold": 0.3,
        "warning_threshold": 0.65,
        "daily_budget_usd": 20.0,
        "monthly_budget_usd": 100.0,
        "budget_alert_pct": 80,
    }
    if not path.exists():
        return defaults
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    qs = cfg.get("quality_score", {})
    budget = cfg.get("budget", {})
    return {
        "critical_threshold": float(qs.get("critical_threshold", defaults["critical_threshold"])),
        "warning_threshold": float(qs.get("warning_threshold", defaults["warning_threshold"])),
        "daily_budget_usd": float(budget.get("daily_usd", defaults["daily_budget_usd"])),
        "monthly_budget_usd": float(budget.get("monthly_usd", defaults["monthly_budget_usd"])),
        "budget_alert_pct": int(budget.get("alert_pct", defaults["budget_alert_pct"])),
    }


@router.get("/dashboard/quality-warnings")
async def get_quality_warnings(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Invocations with quality_score below configurable thresholds.

    Reads thresholds from config/dashboard.yaml. Returns time series
    of warning/critical counts and a breakdown by task_type.
    """
    conn = request.app.state.db.connection
    since = _days_ago(days)
    thresholds = _load_dashboard_config(request.app.state.config_dir)
    warn_thresh = thresholds["warning_threshold"]
    crit_thresh = thresholds["critical_threshold"]

    # Daily warning/critical counts
    cursor = await conn.execute(
        """SELECT DATE(timestamp) as day,
               COUNT(CASE WHEN quality_score < ? AND quality_score >= ? THEN 1 END) as warnings,
               COUNT(CASE WHEN quality_score < ? THEN 1 END) as criticals
           FROM invocation_log
           WHERE timestamp >= ? AND quality_score IS NOT NULL AND is_shadow = 0
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (warn_thresh, crit_thresh, crit_thresh, since),
    )
    time_series = [
        {"date": row[0], "warnings": row[1], "criticals": row[2]}
        for row in await cursor.fetchall()
    ]

    # Totals
    cursor = await conn.execute(
        """SELECT
               COUNT(CASE WHEN quality_score < ? AND quality_score >= ? THEN 1 END),
               COUNT(CASE WHEN quality_score < ? THEN 1 END),
               COUNT(*)
           FROM invocation_log
           WHERE timestamp >= ? AND quality_score IS NOT NULL AND is_shadow = 0""",
        (warn_thresh, crit_thresh, crit_thresh, since),
    )
    row = await cursor.fetchone()
    total_warnings = row[0]
    total_criticals = row[1]
    total_scored = row[2]

    # Breakdown by task_type
    cursor = await conn.execute(
        """SELECT task_type,
               COUNT(CASE WHEN quality_score < ? AND quality_score >= ? THEN 1 END) as warnings,
               COUNT(CASE WHEN quality_score < ? THEN 1 END) as criticals,
               COUNT(*) as total_scored
           FROM invocation_log
           WHERE timestamp >= ? AND quality_score IS NOT NULL AND is_shadow = 0
           GROUP BY task_type
           HAVING warnings > 0 OR criticals > 0
           ORDER BY criticals DESC, warnings DESC""",
        (warn_thresh, crit_thresh, crit_thresh, since),
    )
    by_task_type = [
        {
            "task_type": row[0],
            "warnings": row[1],
            "criticals": row[2],
            "total_scored": row[3],
        }
        for row in await cursor.fetchall()
    ]

    return {
        "summary": {
            "total_warnings": total_warnings,
            "total_criticals": total_criticals,
            "total_scored": total_scored,
            "warning_rate_pct": round(
                (total_warnings + total_criticals) / total_scored * 100, 1
            )
            if total_scored > 0
            else 0.0,
        },
        "thresholds": thresholds,
        "time_series": time_series,
        "by_task_type": by_task_type,
        "days": days,
    }
