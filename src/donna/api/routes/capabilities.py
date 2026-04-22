"""Read-only API routes for the capability registry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from donna.capabilities.models import SELECT_CAPABILITY, CapabilityRow, row_to_capability

router = APIRouter()


def _capability_to_dict(cap: CapabilityRow) -> dict[str, Any]:
    return {
        "id": cap.id,
        "name": cap.name,
        "description": cap.description,
        "input_schema": cap.input_schema,
        "trigger_type": cap.trigger_type,
        "status": cap.status,
        "created_at": str(cap.created_at),
        "created_by": cap.created_by,
        "notes": cap.notes,
    }


@router.get("/capabilities")
async def list_capabilities(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    conn = request.app.state.db.connection

    if status is not None:
        cursor = await conn.execute(
            f"SELECT {SELECT_CAPABILITY} FROM capability WHERE status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SELECT_CAPABILITY} FROM capability ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    rows = await cursor.fetchall()
    caps = [_capability_to_dict(row_to_capability(r)) for r in rows]
    return {"capabilities": caps, "count": len(caps)}


@router.get("/capabilities/{name}")
async def get_capability(name: str, request: Request) -> dict[str, Any]:
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        f"SELECT {SELECT_CAPABILITY} FROM capability WHERE name = ?",
        (name,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Capability '{name}' not found")
    return _capability_to_dict(row_to_capability(row))
