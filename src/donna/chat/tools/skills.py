"""Read tool handlers for the skills and skill_candidate_report tables.

Provides three query handlers for the chat tool-use agent loop:
- query_skills: list with optional status filter
- get_skill_detail: single skill lookup
- query_skill_candidates: list candidates with optional status filter

See spec_v3.md §9 and docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.chat.tools import ToolContext, ToolResult

logger = structlog.get_logger()


async def query_skills(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Query the skills table with optional status filter.

    Args:
        params: Query parameters — status.
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with matching skill rows and total_count.
    """
    where_clauses: list[str] = []
    bind_params: list[Any] = []

    status: str | None = params.get("status")

    if status is not None:
        where_clauses.append("status = ?")
        bind_params.append(status)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    rows = await ctx.db.execute_sql(
        f"""SELECT name, status, description, run_count, last_run_at, avg_quality
            FROM skills
            WHERE {where}
            ORDER BY name""",
        bind_params,
    )

    logger.debug("query_skills", returned=len(rows))

    return ToolResult(results=rows, total_count=len(rows))


async def get_skill_detail(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Fetch a single skill row with all columns.

    Args:
        params: Must contain ``skill_name`` (str).
        ctx: Tool execution context.

    Returns:
        ToolResult with one row or empty if not found.

    Raises:
        KeyError: If ``skill_name`` is missing from params.
    """
    skill_name: str = params["skill_name"]

    rows = await ctx.db.execute_sql(
        "SELECT * FROM skills WHERE name = ?",
        [skill_name],
    )

    if not rows:
        logger.debug("get_skill_detail_not_found", skill_name=skill_name)
        return ToolResult(results=[], total_count=0)

    return ToolResult(results=rows[:1], total_count=1)


async def query_skill_candidates(
    params: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Query the skill_candidate_report table with optional status filter.

    Args:
        params: Query parameters — status.
        ctx: Tool execution context (db, user_id, session_id).

    Returns:
        ToolResult with matching candidate rows and total_count.
    """
    where_clauses: list[str] = []
    bind_params: list[Any] = []

    status: str | None = params.get("status")

    if status is not None:
        where_clauses.append("status = ?")
        bind_params.append(status)

    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    rows = await ctx.db.execute_sql(
        f"""SELECT name, status, confidence, recommendation, source, created_at
            FROM skill_candidate_report
            WHERE {where}
            ORDER BY created_at DESC""",
        bind_params,
    )

    logger.debug("query_skill_candidates", returned=len(rows))

    return ToolResult(results=rows, total_count=len(rows))
