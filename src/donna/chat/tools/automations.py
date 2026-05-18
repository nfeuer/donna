"""Read tool handlers for the automations table.

Provides two query handlers for the chat tool-use agent loop:
- query_automations: list with optional filtering
- get_automation_detail: single row lookup with full config

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.chat.tools import ToolContext, ToolResult

logger = structlog.get_logger()


async def query_automations(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Query the automations table with optional filters.

    Args:
        params: Query parameters — active_only (default True), skill_name.
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with matching automation rows and total_count.
    """
    where_clauses: list[str] = []
    bind_params: list[Any] = []

    active_only: bool = params.get("active_only", True)
    skill_name: str | None = params.get("skill_name")

    if active_only:
        where_clauses.append("active = 1")
    if skill_name is not None:
        where_clauses.append("skill_name = ?")
        bind_params.append(skill_name)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    rows = await ctx.db.execute_sql(
        f"""SELECT id, name, active, cadence, skill_name,
                   last_run_at, next_run_at, run_count
            FROM automations
            WHERE {where}
            ORDER BY name""",
        bind_params,
    )

    logger.debug("query_automations", returned=len(rows))

    return ToolResult(results=rows, total_count=len(rows))


async def get_automation_detail(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Fetch a single automation row with all columns including full config.

    Args:
        params: Must contain ``automation_id`` (str).
        ctx: Tool execution context.

    Returns:
        ToolResult with one row or empty if not found.

    Raises:
        KeyError: If ``automation_id`` is missing from params.
    """
    automation_id: str = params["automation_id"]

    rows = await ctx.db.execute_sql(
        "SELECT * FROM automations WHERE id = ?",
        [automation_id],
    )

    if not rows:
        logger.debug("get_automation_detail_not_found", automation_id=automation_id)
        return ToolResult(results=[], total_count=0)

    return ToolResult(results=rows[:1], total_count=1)
