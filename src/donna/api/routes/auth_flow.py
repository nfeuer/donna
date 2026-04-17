"""/auth/* routes: request-access, verify, status, logout.

Public routes — deliberately not gated by IP or Immich. The request body
is the ONLY input; responses are constant-time and enumeration-resistant.
"""

from __future__ import annotations

import hashlib

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from donna.api.auth import (
    email_allowlist,
    email_sender,
    ip_gate,
    trusted_proxies,
    verification_tokens,
)

logger = structlog.get_logger()
router = APIRouter()


class RequestAccessBody(BaseModel):
    email: str


class VerifyBody(BaseModel):
    token: str


_GENERIC_ACCEPTED = {
    "status": "accepted",
    "message": "If your email is on file, a verification link has been sent.",
}


@router.post("/request-access", status_code=status.HTTP_202_ACCEPTED)
async def request_access(body: RequestAccessBody, request: Request) -> dict:
    ctx = request.app.state.auth_context
    cfg = request.app.state.auth_config

    raw_email = body.email
    if not email_allowlist.is_valid_email(raw_email):
        logger.info(
            "auth_request_access_invalid_email",
            email_sha256=hashlib.sha256(raw_email.encode()).hexdigest(),
        )
        return _GENERIC_ACCEPTED

    email = email_allowlist.normalize_email(raw_email)
    if not await email_allowlist.is_allowed(ctx.conn, email):
        logger.info(
            "auth_request_access_unknown_email",
            email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        )
        return _GENERIC_ACCEPTED

    client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
    raw_token = await verification_tokens.create(
        ctx.conn,
        ip=client_host,
        email=email,
        expiry_minutes=cfg.email.token_expiry_minutes,
        trust_duration=cfg.ip_gate.default_trust_duration,
    )

    gmail = request.app.state.gmail
    try:
        await email_sender.send_magic_link(
            gmail,
            to=email,
            token=raw_token,
            verify_base_url=cfg.email.verify_base_url,
        )
    except Exception as exc:
        logger.error("auth_request_access_email_send_failed", error=str(exc))
    return _GENERIC_ACCEPTED


async def _consume_verification_token(request: Request, token: str) -> tuple[bool, dict]:
    """Validate + burn the magic-link token and trust the client IP.

    Returns (ok, payload). `ok=False` means the token was invalid/expired
    and `payload` is the error body.
    """
    ctx = request.app.state.auth_context
    cfg = request.app.state.auth_config

    client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
    record = await verification_tokens.validate(ctx.conn, token=token, ip=client_host)
    if record is None:
        return False, {"error": "token_invalid_or_expired"}

    await verification_tokens.mark_used(ctx.conn, token=token)
    await ip_gate.insert_pending_ip(ctx.conn, client_host)
    await ip_gate.trust_ip(
        ctx.conn,
        client_host,
        access_level="user",
        trust_duration=record["trust_duration"],
        verified_by=record["email"],
    )
    return True, {"trusted": True, "next": "immich_login"}


@router.post("/verify")
async def verify(body: VerifyBody, request: Request):
    ok, payload = await _consume_verification_token(request, body.token)
    if not ok:
        return JSONResponse(status_code=400, content=payload)
    return payload


@router.get("/verify")
async def verify_from_email(token: str, request: Request):
    """Email magic-link target (GET).

    Users click the link directly from their inbox — serve a minimal HTML
    confirmation so a browser can render the result. The token itself is
    single-use and IP-bound; redirecting or rendering nothing would leave
    the user confused about what happened.
    """
    ok, _payload = await _consume_verification_token(request, token)
    if not ok:
        return HTMLResponse(
            status_code=400,
            content=(
                "<!doctype html><meta charset=utf-8>"
                "<title>Donna — link expired</title>"
                "<h1>Link expired or already used</h1>"
                "<p>Request a new verification link.</p>"
            ),
        )
    return HTMLResponse(
        content=(
            "<!doctype html><meta charset=utf-8>"
            "<title>Donna — verified</title>"
            "<h1>This device is now trusted.</h1>"
            "<p>Sign in to Immich to finish linking your identity.</p>"
        ),
    )


@router.get("/status")
async def auth_status(request: Request) -> dict:
    ctx = request.app.state.auth_context
    cfg = request.app.state.auth_config
    client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
    result = await ip_gate.check_ip_access(ctx.conn, client_host)
    return {"trusted": result["action"] == "allow", "reason": result["reason"]}


@router.post("/logout")
async def logout(request: Request):
    """Clear the donna_device cookie and revoke the underlying token row."""
    from donna.api.auth import device_tokens

    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie(
        "donna_device",
        domain=None,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    raw = request.cookies.get("donna_device") or ""
    if raw:
        cfg = request.app.state.auth_config
        ctx = request.app.state.auth_context
        client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
        row = await device_tokens.validate(
            ctx.conn,
            token=raw,
            ip=client_host,
            sliding_window_days=cfg.device_tokens.sliding_window_days,
            absolute_max_days=cfg.device_tokens.absolute_max_days,
        )
        if row:
            await device_tokens.revoke(ctx.conn, device_id=row["id"], revoked_by="self")
    return resp
