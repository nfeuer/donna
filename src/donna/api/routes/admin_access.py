"""Admin access management: trusted IPs, device tokens, service callers."""

from __future__ import annotations

from typing import Any

from fastapi import Body, Request

from donna.api.auth import CurrentAdmin, admin_router, device_tokens, ip_gate, service_keys

router = admin_router()


@router.get("/ips")
async def list_ips(
    request: Request,
    user_id: CurrentAdmin,
    status_filter: str = "all",
) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    if status_filter == "all":
        cursor = await conn.execute(
            "SELECT * FROM trusted_ips ORDER BY created_at DESC LIMIT 500"
        )
    else:
        cursor = await conn.execute(
            "SELECT * FROM trusted_ips WHERE status=? ORDER BY created_at DESC LIMIT 500",
            (status_filter,),
        )
    rows = await cursor.fetchall()
    await cursor.close()
    return {"ips": [dict(r) for r in rows]}


@router.post("/ips/{ip}/revoke")
async def revoke_ip_route(
    request: Request,
    ip: str,
    user_id: CurrentAdmin,
    body: dict = Body(default_factory=dict),
) -> dict[str, str]:
    conn = request.app.state.auth_context.conn
    reason = body.get("reason") if isinstance(body, dict) else None
    await ip_gate.revoke_ip(conn, ip, revoked_by=user_id, reason=reason)
    return {"status": "revoked", "ip": ip}


@router.post("/ips/{ip}/trust")
async def trust_ip_route(
    request: Request,
    ip: str,
    user_id: CurrentAdmin,
    body: dict = Body(default_factory=dict),
) -> dict[str, str]:
    conn = request.app.state.auth_context.conn
    access_level = body.get("access_level", "user")
    trust_duration = body.get("trust_duration", "30d")
    await ip_gate.insert_pending_ip(conn, ip)
    await ip_gate.trust_ip(
        conn,
        ip,
        access_level=access_level,
        trust_duration=trust_duration,
        verified_by=user_id,
    )
    return {"status": "trusted", "ip": ip}


@router.get("/devices")
async def list_devices(
    request: Request,
    user_id: CurrentAdmin,
    target_user: str | None = None,
) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    target = target_user or user_id
    devices = await device_tokens.list_for_user(conn, user_id=target)
    return {"devices": devices}


@router.post("/devices/{device_id}/revoke")
async def revoke_device(
    request: Request,
    device_id: int,
    user_id: CurrentAdmin,
) -> dict[str, str]:
    conn = request.app.state.auth_context.conn
    await device_tokens.revoke(conn, device_id=device_id, revoked_by=user_id)
    return {"status": "revoked", "device_id": str(device_id)}


@router.get("/callers")
async def list_callers(request: Request, user_id: CurrentAdmin) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    cursor = await conn.execute(
        "SELECT id, caller_id, monthly_budget_usd, enabled, created_at, revoked_at "
        "FROM llm_gateway_callers ORDER BY caller_id"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return {"callers": [dict(r) for r in rows]}


@router.post("/callers/{caller_id}/rotate")
async def rotate_caller(
    request: Request,
    caller_id: str,
    user_id: CurrentAdmin,
    body: dict = Body(default_factory=dict),
) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    budget = float(body.get("monthly_budget_usd", 0.0))
    raw = await service_keys.seed_or_rotate(
        conn, caller_id=caller_id, monthly_budget_usd=budget,
    )
    return {
        "caller_id": caller_id,
        "api_key": raw,
        "monthly_budget_usd": budget,
    }
