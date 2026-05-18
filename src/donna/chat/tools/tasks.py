"""Read tool handlers for the tasks table.

Provides two query handlers for the chat tool-use agent loop:
- query_tasks: paginated list with filtering and sorting
- get_task_detail: single row lookup with all columns

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.chat.tools import ToolContext, ToolResult

logger = structlog.get_logger()

# Allowed sort column names (allowlist prevents SQL injection)
_SORT_COLUMNS: dict[str, str] = {
    "priority": "priority",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "deadline": "deadline",
}

_DEFAULT_SORT = "priority"
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100


async def query_tasks(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Query the tasks table with optional filters, sorting, and pagination.

    Args:
        params: Query parameters — status, priority, domain, title_search,
            created_after, updated_after, sort, limit.
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with matching task rows and total_count.
    """
    where_clauses: list[str] = ["user_id = ?"]
    bind_params: list[Any] = [ctx.user_id]

    status: str | None = params.get("status")
    priority: int | None = params.get("priority")
    domain: str | None = params.get("domain")
    title_search: str | None = params.get("title_search")
    created_after: str | None = params.get("created_after")
    updated_after: str | None = params.get("updated_after")

    if status is not None:
        where_clauses.append("status = ?")
        bind_params.append(status)
    if priority is not None:
        where_clauses.append("priority = ?")
        bind_params.append(priority)
    if domain is not None:
        where_clauses.append("domain = ?")
        bind_params.append(domain)
    if title_search is not None:
        where_clauses.append("title LIKE ?")
        bind_params.append(f"%{title_search}%")
    if created_after is not None:
        where_clauses.append("created_at >= ?")
        bind_params.append(created_after)
    if updated_after is not None:
        where_clauses.append("updated_at >= ?")
        bind_params.append(updated_after)

    where = " AND ".join(where_clauses)

    # --- total count ---
    count_rows = await ctx.db.execute_sql(
        f"SELECT COUNT(*) AS count FROM tasks WHERE {where}",
        list(bind_params),
    )
    total_count: int = count_rows[0]["count"] if count_rows else 0

    # --- sort ---
    sort_key: str = params.get("sort", _DEFAULT_SORT)
    sort_col = _SORT_COLUMNS.get(sort_key, _SORT_COLUMNS[_DEFAULT_SORT])

    # --- limit ---
    limit_raw = params.get("limit", _DEFAULT_LIMIT)
    limit = min(int(limit_raw), _MAX_LIMIT)

    data_params = [*list(bind_params), limit]

    # Safe: sort_col comes from allowlist; user values go through bind_params
    data_rows = await ctx.db.execute_sql(
        f"""SELECT id, title, description, status, priority, domain,
                   notes, created_at, updated_at, scheduled_start, deadline
            FROM tasks
            WHERE {where}
            ORDER BY {sort_col}
            LIMIT ?""",
        data_params,
    )

    truncated = total_count > len(data_rows)

    logger.debug(
        "query_tasks",
        total=total_count,
        returned=len(data_rows),
        limit=limit,
    )

    return ToolResult(
        results=data_rows,
        total_count=total_count,
        truncated=truncated,
    )


async def get_task_detail(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Fetch a single task row with all columns.

    Args:
        params: Must contain ``task_id`` (str).
        ctx: Tool execution context.

    Returns:
        ToolResult with one row or empty if not found.

    Raises:
        KeyError: If ``task_id`` is missing from params.
    """
    task_id: str = params["task_id"]

    rows = await ctx.db.execute_sql(
        """SELECT id, title, description, status, priority, domain,
                  notes, created_at, updated_at, scheduled_start, deadline
           FROM tasks
           WHERE id = ?""",
        [task_id],
    )

    if not rows:
        logger.debug("get_task_detail_not_found", task_id=task_id)
        return ToolResult(results=[], total_count=0)

    return ToolResult(results=rows[:1], total_count=1)
