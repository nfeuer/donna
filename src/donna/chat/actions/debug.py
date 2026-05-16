"""Chat action handlers for debug/system operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def get_debug_data(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    data: dict[str, Any] = {}

    try:
        if hasattr(ctx.db, "get_invocation_stats"):
            stats = await ctx.db.get_invocation_stats()
            data["invocation_stats"] = stats

        if hasattr(ctx.db, "list_tasks"):
            all_tasks = await ctx.db.list_tasks(user_id=ctx.user_id)
            status_counts: dict[str, int] = {}
            for t in all_tasks:
                status_counts[t.status] = status_counts.get(t.status, 0) + 1
            data["task_status_counts"] = status_counts
            data["total_tasks"] = len(all_tasks)

    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to gather debug data: {exc}")

    return ActionResult(
        success=True,
        data=data,
        summary=f"System debug data: {len(data)} sections.",
    )


async def get_agent_status(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    agent_name = params.get("agent_name")

    try:
        if hasattr(ctx.db, "list_agent_runs"):
            runs = await ctx.db.list_agent_runs(agent_name=agent_name, limit=10)
        elif hasattr(ctx.db, "list_skill_runs"):
            runs = await ctx.db.list_skill_runs(skill_name=agent_name, limit=10)
        else:
            return ActionResult(success=False, error="Agent status not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to get agent status: {exc}")

    run_list = [
        {
            "id": getattr(r, "id", str(r)),
            "status": getattr(r, "status", "unknown"),
            "started_at": str(getattr(r, "started_at", "")),
            "skill_name": getattr(r, "skill_name", agent_name or ""),
        }
        for r in runs
    ]
    return ActionResult(
        success=True,
        data={"runs": run_list, "count": len(run_list)},
        summary=f"Found {len(run_list)} recent run(s){f' for {agent_name}' if agent_name else ''}.",
    )
