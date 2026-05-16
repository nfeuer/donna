"""Chat action handlers for automation operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def create_automation(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    name = params.get("name", "")
    trigger = params.get("trigger", "")
    skill_name = params.get("skill_name", "")

    if not name or not trigger or not skill_name:
        return ActionResult(success=False, error="Name, trigger, and skill_name are required.")

    try:
        if hasattr(ctx.db, "create_automation"):
            auto_id = await ctx.db.create_automation(
                name=name, trigger=trigger, skill_name=skill_name, user_id=ctx.user_id,
            )
            return ActionResult(
                success=True,
                data={"id": auto_id, "name": name, "trigger": trigger, "skill_name": skill_name},
                summary=f"Created automation '{name}' (trigger: {trigger}, skill: {skill_name}).",
            )
        return ActionResult(success=False, error="Automation creation not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to create automation: {exc}")


async def list_automations(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    try:
        if hasattr(ctx.db, "list_automations"):
            automations = await ctx.db.list_automations()
        else:
            return ActionResult(success=False, error="Automation listing not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to list automations: {exc}")

    auto_list = [
        {
            "name": getattr(a, "name", str(a)),
            "trigger": getattr(a, "trigger_type", "unknown"),
            "active": getattr(a, "active", True),
            "skill_name": getattr(a, "skill_name", ""),
        }
        for a in automations
    ]
    return ActionResult(
        success=True,
        data={"automations": auto_list, "count": len(auto_list)},
        summary=f"Found {len(auto_list)} automation(s).",
    )
