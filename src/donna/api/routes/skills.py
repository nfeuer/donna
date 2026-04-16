"""Read-only API routes for the skill system."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from donna.skills.models import (
    SELECT_SKILL,
    SELECT_SKILL_VERSION,
    SkillRow,
    SkillVersionRow,
    row_to_skill,
    row_to_skill_version,
)

router = APIRouter()


def _skill_to_dict(skill: SkillRow, version: SkillVersionRow | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": skill.id,
        "capability_name": skill.capability_name,
        "state": skill.state,
        "requires_human_gate": skill.requires_human_gate,
        "baseline_agreement": skill.baseline_agreement,
        "current_version_id": skill.current_version_id,
        "created_at": str(skill.created_at),
        "updated_at": str(skill.updated_at),
    }
    if version is not None:
        data["current_version"] = {
            "id": version.id,
            "version_number": version.version_number,
            "yaml_backbone": version.yaml_backbone,
            "step_content": version.step_content,
            "output_schemas": version.output_schemas,
            "created_by": version.created_by,
            "changelog": version.changelog,
        }
    return data


@router.get("/skills")
async def list_skills(
    request: Request,
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    conn = request.app.state.db.connection

    if state is not None:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL} FROM skill WHERE state = ? ORDER BY updated_at DESC LIMIT ?",
            (state, limit),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL} FROM skill ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )

    rows = await cursor.fetchall()
    skills = [_skill_to_dict(row_to_skill(r)) for r in rows]
    return {"skills": skills, "count": len(skills)}


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection

    cursor = await conn.execute(
        f"SELECT {SELECT_SKILL} FROM skill WHERE id = ?",
        (skill_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    skill = row_to_skill(row)
    version = None
    if skill.current_version_id:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL_VERSION} FROM skill_version WHERE id = ?",
            (skill.current_version_id,),
        )
        vrow = await cursor.fetchone()
        if vrow:
            version = row_to_skill_version(vrow)

    return _skill_to_dict(skill, version)
