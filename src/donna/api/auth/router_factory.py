"""APIRouter factories with auth dependencies pre-bound.

Use these INSTEAD of bare `APIRouter()` when mounting routes. Each factory
is the single public way to get a router of its auth class, which makes
"deny by default" structurally enforced.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from donna.api.auth import dependencies as dep


async def _user_dep(request: Request) -> str:
    ctx: dep.AuthContext = request.app.state.auth_context
    cfg = request.app.state.auth_config
    return await dep._resolve_user_id(
        request,
        ctx,
        sliding_window_days=cfg.device_tokens.sliding_window_days,
        absolute_max_days=cfg.device_tokens.absolute_max_days,
        trusted_proxies=cfg.trusted_proxies,
    )


async def _admin_dep(request: Request) -> str:
    ctx: dep.AuthContext = request.app.state.auth_context
    cfg = request.app.state.auth_config
    return await dep._resolve_admin_user_id(
        request,
        ctx,
        sliding_window_days=cfg.device_tokens.sliding_window_days,
        absolute_max_days=cfg.device_tokens.absolute_max_days,
        trusted_proxies=cfg.trusted_proxies,
    )


async def _service_dep(request: Request) -> dict:
    from donna.api.auth import service_keys

    ctx: dep.AuthContext = request.app.state.auth_context
    cfg = request.app.state.auth_config
    ip = dep.trusted_proxies_module_client_ip(request, cfg.trusted_proxies)
    key = request.headers.get("x-donna-service-key", "")
    forwarded_host = request.headers.get("x-forwarded-host")
    caller = await service_keys.validate(
        ctx.conn,
        presented_key=key,
        source_ip=ip,
        internal_cidrs=cfg.internal_cidrs,
        forwarded_host=forwarded_host,
    )
    if caller is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "service_key_invalid"},
        )
    return caller


CurrentUser = Annotated[str, Depends(_user_dep)]
CurrentAdmin = Annotated[str, Depends(_admin_dep)]
CurrentServiceCaller = Annotated[dict, Depends(_service_dep)]


def public_liveness_router() -> APIRouter:
    return APIRouter()


def public_auth_router() -> APIRouter:
    return APIRouter()


def public_webhook_twilio_router() -> APIRouter:
    return APIRouter()


def user_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(_user_dep)])


def admin_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(_admin_dep)])


def service_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(_service_dep)])
