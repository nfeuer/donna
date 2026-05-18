"""Read tool handlers for the invocation_log table.

Provides three query handlers for the chat tool-use agent loop:
- query_invocations: paginated list with filtering and sorting
- get_invocation_detail: single row lookup with full columns
- query_invocation_stats: GROUP BY aggregations

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.chat.tools import ToolContext, ToolResult

logger = structlog.get_logger()

# Allowed sort column names (allowlist prevents SQL injection)
_SORT_COLUMNS: dict[str, str] = {
    "cost": "cost_usd",
    "latency": "latency_ms",
    "timestamp": "timestamp",
    "tokens_in": "tokens_in",
}

# Allowed sort directions
_SORT_DIRS = {"asc", "desc"}

# Allowed group_by keys
_GROUP_BY_COLUMNS: dict[str, str] = {
    "task_type": "task_type",
    "model": "model_alias",
    "date": "date(timestamp)",
}

_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100


async def query_invocations(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Query invocation_log with optional filters, sorting, and pagination.

    Args:
        params: Query parameters — date_from, date_to, task_type, model,
            min_cost, min_latency, has_error, sort, sort_dir, limit.
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with matching invocation rows and total_count.
    """
    where_clauses: list[str] = []
    bind_params: list[Any] = []

    date_from: str | None = params.get("date_from")
    date_to: str | None = params.get("date_to")
    task_type: str | None = params.get("task_type")
    model: str | None = params.get("model")
    min_cost: float | None = params.get("min_cost")
    min_latency: float | None = params.get("min_latency")
    has_error: bool | None = params.get("has_error")

    if date_from is not None:
        where_clauses.append("timestamp >= ?")
        bind_params.append(date_from)
    if date_to is not None:
        where_clauses.append("timestamp <= ?")
        bind_params.append(date_to)
    if task_type is not None:
        where_clauses.append("task_type = ?")
        bind_params.append(task_type)
    if model is not None:
        where_clauses.append("model_alias = ?")
        bind_params.append(model)
    if min_cost is not None:
        where_clauses.append("cost_usd >= ?")
        bind_params.append(min_cost)
    if min_latency is not None:
        where_clauses.append("latency_ms >= ?")
        bind_params.append(min_latency)
    if has_error is not None:
        where_clauses.append("has_error = ?")
        bind_params.append(has_error)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    # --- total count ---
    count_rows = await ctx.db.execute_sql(
        f"SELECT COUNT(*) AS count FROM invocation_log WHERE {where}",
        list(bind_params),
    )
    total_count: int = count_rows[0]["count"] if count_rows else 0

    # --- sort ---
    sort_key: str = params.get("sort", "timestamp")
    sort_col = _SORT_COLUMNS.get(sort_key, "timestamp")
    sort_dir_raw: str = params.get("sort_dir", "desc")
    sort_dir = sort_dir_raw.lower() if sort_dir_raw.lower() in _SORT_DIRS else "desc"

    # --- limit ---
    limit_raw = params.get("limit", _DEFAULT_LIMIT)
    limit = min(int(limit_raw), _MAX_LIMIT)

    data_params = [*list(bind_params), limit]

    # Safe: sort_col and sort_dir come from allowlists; user values go through bind_params
    data_rows = await ctx.db.execute_sql(
        f"""SELECT id, task_type, model_alias, model_actual,
                   tokens_in, tokens_out, cost_usd, latency_ms,
                   quality_score, timestamp, has_error
            FROM invocation_log
            WHERE {where}
            ORDER BY {sort_col} {sort_dir}
            LIMIT ?""",
        data_params,
    )

    truncated = total_count > len(data_rows)

    logger.debug(
        "query_invocations",
        total=total_count,
        returned=len(data_rows),
        limit=limit,
    )

    return ToolResult(
        results=data_rows,
        total_count=total_count,
        truncated=truncated,
    )


async def get_invocation_detail(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Fetch a single invocation row with all columns including payload_path and trace_id.

    Args:
        params: Must contain ``invocation_id`` (str).
        ctx: Tool execution context.

    Returns:
        ToolResult with one row or empty if not found.

    Raises:
        KeyError: If ``invocation_id`` is missing from params.
    """
    invocation_id: str = params["invocation_id"]

    rows = await ctx.db.execute_sql(
        """SELECT id, task_type, model_alias, model_actual,
                  tokens_in, tokens_out, cost_usd, latency_ms,
                  quality_score, timestamp, has_error,
                  input_hash, task_id, output, is_shadow,
                  payload_path, trace_id, user_id, skill_id,
                  escalation_request_id, estimated_tokens_in, overflow_escalated
           FROM invocation_log
           WHERE id = ?""",
        [invocation_id],
    )

    if not rows:
        logger.debug("get_invocation_detail_not_found", invocation_id=invocation_id)
        return ToolResult(results=[], total_count=0)

    return ToolResult(results=rows[:1], total_count=1)


async def query_invocation_stats(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Aggregate invocation_log stats grouped by task_type, model, or date.

    Args:
        params: Must contain ``group_by`` (task_type|model|date).
            Optional: date_from, date_to.
        ctx: Tool execution context.

    Returns:
        ToolResult with one row per group, containing count, cost, latency,
        quality, and token aggregates.

    Raises:
        KeyError: If ``group_by`` is missing from params.
        ValueError: If ``group_by`` is not one of the allowed values.
    """
    group_by_key: str = params["group_by"]
    if group_by_key not in _GROUP_BY_COLUMNS:
        raise ValueError(
            f"Invalid group_by: {group_by_key!r}. "
            f"Must be one of: {', '.join(_GROUP_BY_COLUMNS)}"
        )
    group_col = _GROUP_BY_COLUMNS[group_by_key]

    where_clauses: list[str] = []
    bind_params: list[Any] = []

    date_from: str | None = params.get("date_from")
    date_to: str | None = params.get("date_to")

    if date_from is not None:
        where_clauses.append("timestamp >= ?")
        bind_params.append(date_from)
    if date_to is not None:
        where_clauses.append("timestamp <= ?")
        bind_params.append(date_to)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Safe: group_col comes from allowlist; user values go through bind_params
    rows = await ctx.db.execute_sql(
        f"""SELECT {group_col} AS group_key,
                   COUNT(*) AS count,
                   SUM(cost_usd) AS total_cost,
                   AVG(cost_usd) AS avg_cost,
                   AVG(latency_ms) AS avg_latency,
                   AVG(quality_score) AS avg_quality,
                   SUM(tokens_in) AS total_tokens_in,
                   SUM(tokens_out) AS total_tokens_out
            FROM invocation_log
            WHERE {where}
            GROUP BY {group_col}
            ORDER BY count DESC""",
        bind_params,
    )

    logger.debug("query_invocation_stats", group_by=group_by_key, rows=len(rows))

    return ToolResult(results=rows, total_count=len(rows))
