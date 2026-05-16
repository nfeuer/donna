"""Chat action handlers for skill operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def execute_skill(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    skill_name = params.get("skill_name", "")
    input_data = params.get("input_data", {})

    if not skill_name:
        return ActionResult(success=False, error="skill_name is required.")

    try:
        if hasattr(ctx.db, "get_skill"):
            skill = await ctx.db.get_skill(skill_name)
            if skill is None:
                return ActionResult(success=False, error=f"Skill '{skill_name}' not found.")

        if hasattr(ctx.db, "queue_skill_run"):
            run_id = await ctx.db.queue_skill_run(
                skill_name=skill_name, input_data=input_data, user_id=ctx.user_id,
            )
            return ActionResult(
                success=True,
                data={"run_id": run_id, "skill_name": skill_name, "status": "queued"},
                summary=f"Skill '{skill_name}' queued for execution (run: {run_id}).",
            )

        return ActionResult(success=False, error="Skill execution not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to execute skill: {exc}")


async def list_skills(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    try:
        if hasattr(ctx.db, "list_skills"):
            skills = await ctx.db.list_skills()
        else:
            return ActionResult(success=False, error="Skill listing not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to list skills: {exc}")

    skill_list = [
        {"name": getattr(s, "name", str(s)), "status": getattr(s, "status", "unknown")}
        for s in skills
    ]
    return ActionResult(
        success=True,
        data={"skills": skill_list, "count": len(skill_list)},
        summary=f"Found {len(skill_list)} skill(s).",
    )


async def create_skill_draft(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    name = params.get("name", "")
    description = params.get("description", "")
    steps = params.get("steps", [])

    if not name or not description:
        return ActionResult(success=False, error="Name and description are required.")

    try:
        if hasattr(ctx.db, "create_skill_draft"):
            draft_id = await ctx.db.create_skill_draft(
                name=name, description=description, steps=steps, user_id=ctx.user_id,
            )
            return ActionResult(
                success=True,
                data={"draft_id": draft_id, "name": name},
                summary=f"Created skill draft: {name}",
            )
        return ActionResult(success=False, error="Skill draft creation not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to create skill draft: {exc}")
