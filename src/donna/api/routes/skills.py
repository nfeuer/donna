"""API routes for the skill system."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from donna.skills.models import (
    SELECT_SKILL,
    SELECT_SKILL_VERSION,
    SkillRow,
    SkillVersionRow,
    row_to_skill,
    row_to_skill_version,
)

router = APIRouter()


class TransitionRequest(BaseModel):
    to_state: str
    reason: str
    notes: str | None = None


class HumanGateRequest(BaseModel):
    value: bool


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


@router.post("/skills/{skill_id}/state")
async def transition_skill_state(
    skill_id: str,
    body: TransitionRequest,
    request: Request,
) -> dict:
    """User-initiated state transition via SkillLifecycleManager."""
    conn = request.app.state.db.connection

    # Post Task 14: skill-system background components live in the orchestrator
    # process. SkillLifecycleManager is cheap (just `conn + config`), so build
    # one per request. Tests may still inject their own on app.state.
    lifecycle = getattr(request.app.state, "skill_lifecycle_manager", None)
    if lifecycle is None:
        from donna.skills.lifecycle import SkillLifecycleManager

        skill_config = getattr(request.app.state, "skill_system_config", None)
        if skill_config is None:
            raise HTTPException(
                status_code=503, detail="skill_system_config not loaded"
            )
        lifecycle = SkillLifecycleManager(conn, skill_config)

    from donna.skills.lifecycle import (
        HumanGateRequiredError,
        IllegalTransitionError,
        SkillNotFoundError,
    )
    from donna.tasks.db_models import SkillState

    try:
        to_state = SkillState(body.to_state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid state: {body.to_state}")

    try:
        await lifecycle.transition(
            skill_id=skill_id,
            to_state=to_state,
            reason=body.reason,
            actor="user",
            notes=body.notes,
        )
    except SkillNotFoundError:
        raise HTTPException(status_code=404, detail="skill not found")
    except IllegalTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HumanGateRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    # AS-4.2: Save (reset baseline) — on flagged_for_review → trusted with
    # reason=human_approval, recompute baseline_agreement from recent divergences.
    if body.to_state == "trusted" and body.reason == "human_approval":
        skill_config = request.app.state.skill_system_config
        window = int(skill_config.baseline_reset_window) if skill_config else 100
        cursor = await conn.execute(
            "SELECT AVG(agreement) FROM ("
            "  SELECT d.overall_agreement AS agreement"
            "  FROM skill_divergence d"
            "  JOIN skill_run r ON d.skill_run_id = r.id"
            "  WHERE r.skill_id = ?"
            "  ORDER BY d.created_at DESC LIMIT ?"
            ")",
            (skill_id, window),
        )
        row = await cursor.fetchone()
        if row and row[0] is not None:
            await conn.execute(
                "UPDATE skill SET baseline_agreement = ? WHERE id = ?",
                (float(row[0]), skill_id),
            )
            await conn.commit()

    return {"skill_id": skill_id, "to_state": body.to_state, "ok": True}


@router.post("/skills/{skill_id}/flags/requires_human_gate")
async def set_requires_human_gate(
    skill_id: str,
    body: HumanGateRequest,
    request: Request,
) -> dict:
    """Toggle the requires_human_gate flag on a skill."""
    conn = request.app.state.db.connection
    cursor = await conn.execute("SELECT id FROM skill WHERE id = ?", (skill_id,))
    if await cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="skill not found")

    await conn.execute(
        "UPDATE skill SET requires_human_gate = ?, updated_at = ? WHERE id = ?",
        (1 if body.value else 0, datetime.now(timezone.utc).isoformat(), skill_id),
    )
    await conn.commit()
    return {"skill_id": skill_id, "requires_human_gate": body.value}
