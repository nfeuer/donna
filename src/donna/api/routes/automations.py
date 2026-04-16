"""REST routes for automation CRUD, pause/resume, run-now, and run history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from donna.automations.cron import CronScheduleCalculator, InvalidCronExpressionError
from donna.automations.models import AutomationRow, AutomationRunRow
from donna.automations.repository import AutomationRepository

router = APIRouter()


# ---------------------------------------------------------------------------
# Request body schemas
# ---------------------------------------------------------------------------


class CreateAutomationRequest(BaseModel):
    user_id: str
    name: str
    description: str | None = None
    capability_name: str
    inputs: dict
    trigger_type: str  # "on_schedule" | "on_manual"
    schedule: str | None = None
    alert_conditions: dict = {}
    alert_channels: list = []
    max_cost_per_run_usd: float | None = None
    min_interval_seconds: int = 300
    created_via: str = "dashboard"


class UpdateAutomationRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    inputs: dict | None = None
    schedule: str | None = None
    alert_conditions: dict | None = None
    alert_channels: list | None = None
    max_cost_per_run_usd: float | None = None
    min_interval_seconds: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _automation_to_dict(row: AutomationRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "description": row.description,
        "capability_name": row.capability_name,
        "inputs": row.inputs,
        "trigger_type": row.trigger_type,
        "schedule": row.schedule,
        "alert_conditions": row.alert_conditions,
        "alert_channels": row.alert_channels,
        "max_cost_per_run_usd": row.max_cost_per_run_usd,
        "min_interval_seconds": row.min_interval_seconds,
        "status": row.status,
        "last_run_at": _dt_iso(row.last_run_at),
        "next_run_at": _dt_iso(row.next_run_at),
        "run_count": row.run_count,
        "failure_count": row.failure_count,
        "created_at": _dt_iso(row.created_at),
        "updated_at": _dt_iso(row.updated_at),
        "created_via": row.created_via,
    }


def _run_to_dict(row: AutomationRunRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "automation_id": row.automation_id,
        "started_at": _dt_iso(row.started_at),
        "finished_at": _dt_iso(row.finished_at),
        "status": row.status,
        "execution_path": row.execution_path,
        "skill_run_id": row.skill_run_id,
        "invocation_log_id": row.invocation_log_id,
        "output": row.output,
        "alert_sent": row.alert_sent,
        "alert_content": row.alert_content,
        "error": row.error,
        "cost_usd": row.cost_usd,
    }


def _get_cron(request: Request) -> CronScheduleCalculator:
    return CronScheduleCalculator()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/automations")
async def list_automations(
    request: Request,
    status: str = Query(default="active"),
    capability_name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)
    effective_status = None if status == "all" else status
    rows = await repo.list_all(
        status=effective_status,
        capability_name=capability_name,
        limit=limit,
        offset=offset,
    )
    return {"automations": [_automation_to_dict(r) for r in rows], "count": len(rows)}


@router.get("/automations/{automation_id}")
async def get_automation(automation_id: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)
    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")
    return _automation_to_dict(row)


@router.post("/automations", status_code=201)
async def create_automation(
    body: CreateAutomationRequest,
    request: Request,
) -> dict[str, Any]:
    conn = request.app.state.db.connection

    # Validate capability exists
    cursor = await conn.execute(
        "SELECT 1 FROM capability WHERE name = ?", (body.capability_name,)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(
            status_code=400,
            detail=f"Capability '{body.capability_name}' not found",
        )

    # Compute next_run_at for on_schedule automations
    next_run_at: datetime | None = None
    if body.trigger_type == "on_schedule" and body.schedule:
        cron = _get_cron(request)
        try:
            next_run_at = cron.next_run(
                expression=body.schedule,
                after=datetime.now(timezone.utc),
            )
        except InvalidCronExpressionError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    repo = AutomationRepository(conn)
    auto_id = await repo.create(
        user_id=body.user_id,
        name=body.name,
        description=body.description,
        capability_name=body.capability_name,
        inputs=body.inputs,
        trigger_type=body.trigger_type,
        schedule=body.schedule,
        alert_conditions=body.alert_conditions,
        alert_channels=body.alert_channels,
        max_cost_per_run_usd=body.max_cost_per_run_usd,
        min_interval_seconds=body.min_interval_seconds,
        created_via=body.created_via,
        next_run_at=next_run_at,
    )

    row = await repo.get(auto_id)
    if row is None:
        raise HTTPException(status_code=500, detail="unexpected: row missing after write")
    return _automation_to_dict(row)


@router.patch("/automations/{automation_id}")
async def update_automation(
    automation_id: str,
    body: UpdateAutomationRequest,
    request: Request,
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)

    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.description is not None:
        fields["description"] = body.description
    if body.inputs is not None:
        fields["inputs"] = body.inputs
    if body.alert_conditions is not None:
        fields["alert_conditions"] = body.alert_conditions
    if body.alert_channels is not None:
        fields["alert_channels"] = body.alert_channels
    if body.max_cost_per_run_usd is not None:
        fields["max_cost_per_run_usd"] = body.max_cost_per_run_usd
    if body.min_interval_seconds is not None:
        fields["min_interval_seconds"] = body.min_interval_seconds

    if body.schedule is not None:
        # Validate + recompute next_run_at when schedule changes
        cron = _get_cron(request)
        try:
            next_run_at = cron.next_run(
                expression=body.schedule,
                after=datetime.now(timezone.utc),
            )
        except InvalidCronExpressionError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        fields["schedule"] = body.schedule
        fields["next_run_at"] = next_run_at

    if fields:
        await repo.update_fields(automation_id, **fields)

    updated = await repo.get(automation_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="unexpected: row missing after write")
    return _automation_to_dict(updated)


@router.post("/automations/{automation_id}/pause")
async def pause_automation(automation_id: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)

    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")

    await repo.set_status(automation_id, "paused")
    updated = await repo.get(automation_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="unexpected: row missing after write")
    return _automation_to_dict(updated)


@router.post("/automations/{automation_id}/resume")
async def resume_automation(automation_id: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)

    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")

    fields: dict[str, Any] = {"status": "active"}
    if row.trigger_type == "on_schedule" and row.schedule:
        cron = _get_cron(request)
        try:
            next_run_at = cron.next_run(
                expression=row.schedule,
                after=datetime.now(timezone.utc),
            )
            fields["next_run_at"] = next_run_at
        except InvalidCronExpressionError:
            pass  # leave next_run_at unchanged

    await repo.update_fields(automation_id, **fields)
    updated = await repo.get(automation_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="unexpected: row missing after write")
    return _automation_to_dict(updated)


@router.delete("/automations/{automation_id}")
async def delete_automation(automation_id: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)

    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")

    await repo.set_status(automation_id, "deleted")
    return {"id": automation_id, "status": "deleted"}


@router.post("/automations/{automation_id}/run-now")
async def run_now(automation_id: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)

    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")

    dispatcher = getattr(request.app.state, "automation_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(status_code=503, detail="automation_dispatcher not configured")

    report = await dispatcher.dispatch(row)
    return {
        "automation_id": report.automation_id,
        "run_id": report.run_id,
        "outcome": report.outcome,
        "alert_sent": report.alert_sent,
        "error": report.error,
    }


@router.get("/automations/{automation_id}/runs")
async def get_runs(
    automation_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    repo = AutomationRepository(conn)

    row = await repo.get(automation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Automation '{automation_id}' not found")

    runs = await repo.list_runs(automation_id, limit=limit, offset=offset)
    return {"runs": [_run_to_dict(r) for r in runs], "count": len(runs)}
