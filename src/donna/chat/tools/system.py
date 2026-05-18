"""Read tool handlers for system health and preferences.

Provides two handlers for the chat tool-use agent loop:
- get_system_health: aggregated health metrics from three SQL queries
- query_preferences: list preference_rules with optional filters

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.chat.tools import ToolContext, ToolResult

logger = structlog.get_logger()


async def get_system_health(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Return a snapshot of system health from three database queries.

    Queries:
    1. Error count in the last hour from invocation_log.
    2. Active session count from conversation_sessions.
    3. SQLite database size in megabytes.

    Args:
        params: No parameters required.
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with a single dict containing all health metrics.
    """
    # 1. Error count in the last hour
    error_rows = await ctx.db.execute_sql(
        "SELECT COUNT(*) as cnt FROM invocation_log "
        "WHERE timestamp >= datetime('now', '-1 hour') AND output LIKE '%error%'",
        [],
    )
    error_count: int = error_rows[0]["cnt"] if error_rows else 0

    # 2. Active session count
    session_rows = await ctx.db.execute_sql(
        "SELECT COUNT(*) as cnt FROM conversation_sessions WHERE status = 'active'",
        [],
    )
    active_sessions: int = session_rows[0]["cnt"] if session_rows else 0

    # 3. Database size in MB
    size_rows = await ctx.db.execute_sql(
        "SELECT page_count * page_size / 1024.0 / 1024.0 as size "
        "FROM pragma_page_count(), pragma_page_size()",
        [],
    )
    db_size_mb: float = size_rows[0]["size"] if size_rows else 0.0

    health: dict[str, Any] = {
        "error_count_last_hour": error_count,
        "active_sessions": active_sessions,
        "db_size_mb": db_size_mb,
    }

    logger.debug(
        "get_system_health",
        error_count=error_count,
        active_sessions=active_sessions,
        db_size_mb=db_size_mb,
    )

    return ToolResult(results=[health], total_count=1)


async def query_preferences(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Query the preference_rules table with optional filters.

    Args:
        params: Query parameters — rule_type, enabled_only (default True).
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with matching preference rule rows and total_count.
    """
    where_clauses: list[str] = []
    bind_params: list[Any] = []

    rule_type: str | None = params.get("rule_type")
    enabled_only: bool = params.get("enabled_only", True)

    if enabled_only:
        where_clauses.append("enabled = ?")
        bind_params.append(1)
    if rule_type is not None:
        where_clauses.append("rule_type = ?")
        bind_params.append(rule_type)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    rows = await ctx.db.execute_sql(
        f"""SELECT id, rule_type, rule_text, confidence,
                   enabled, correction_count, created_at
            FROM preference_rules
            WHERE {where}
            ORDER BY confidence DESC""",
        bind_params,
    )

    logger.debug("query_preferences", returned=len(rows))

    return ToolResult(results=rows, total_count=len(rows))
