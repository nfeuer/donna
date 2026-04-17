"""Dependency composition for FastAPI routes.

These are the ONLY functions routes should import from `donna.api.auth`
(plus the type aliases and router factories in router_factory.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from donna.api.auth import device_tokens, ip_gate, trusted_proxies

_DEVICE_COOKIE_NAME = "donna_device"


@dataclass
class AuthContext:
    conn: Any
    auth_config: Any
    immich_client: Any


def _device_token_from_request(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return request.cookies.get(_DEVICE_COOKIE_NAME)


def _immich_bearer_from_request(request: Request) -> str | None:
    """Return the Immich bearer token from cookie or header.

    Device-token scheme uses `Authorization: Bearer` too, so for the
    Immich path we look at the `immich_access_token` cookie first and
    fall back to `X-Immich-Token` header if present.
    """
    cookie = request.cookies.get("immich_access_token")
    if cookie:
        return cookie
    header = request.headers.get("x-immich-token", "")
    return header or None


async def _resolve_user_id(
    request: Request,
    ctx: AuthContext,
    *,
    sliding_window_days: int,
    absolute_max_days: int,
    trusted_proxies: list,
) -> str:
    raw_token = _device_token_from_request(request)
    if raw_token:
        ip = trusted_proxies_module_client_ip(request, trusted_proxies)
        row = await device_tokens.validate(
            ctx.conn,
            token=raw_token,
            ip=ip,
            sliding_window_days=sliding_window_days,
            absolute_max_days=absolute_max_days,
        )
        if row:
            return row["user_id"]

    ip = trusted_proxies_module_client_ip(request, trusted_proxies)
    result = await ip_gate.check_ip_access(ctx.conn, ip, service="donna")
    if result["action"] != "allow":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ip_not_trusted", "step": "request_access"},
        )

    bearer = _immich_bearer_from_request(request)
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthenticated", "step": "immich_login"},
        )
    immich_user = await ctx.immich_client.resolve(bearer=bearer)
    if immich_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "immich_session_invalid"},
        )

    cursor = await ctx.conn.execute(
        "SELECT donna_user_id FROM users WHERE immich_user_id=?",
        (immich_user.immich_user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "user_not_provisioned"},
        )
    return row[0]


async def _resolve_admin_user_id(
    request: Request,
    ctx: AuthContext,
    *,
    sliding_window_days: int,
    absolute_max_days: int,
    trusted_proxies: list,
) -> str:
    ip = trusted_proxies_module_client_ip(request, trusted_proxies)
    result = await ip_gate.check_ip_access(ctx.conn, ip, service="admin")
    if result["action"] != "allow":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ip_not_trusted_admin", "step": "request_access"},
        )
    bearer = _immich_bearer_from_request(request)
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "admin_login_required"},
        )
    immich_user = await ctx.immich_client.resolve(bearer=bearer)
    if immich_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "immich_session_invalid"},
        )
    cursor = await ctx.conn.execute(
        "SELECT donna_user_id, role FROM users WHERE immich_user_id=?",
        (immich_user.immich_user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None or row[1] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_role_required"},
        )
    return row[0]


def trusted_proxies_module_client_ip(request, proxies):
    return trusted_proxies.client_ip(request, trusted_proxies=proxies)
