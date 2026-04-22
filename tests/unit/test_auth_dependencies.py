"""Unit tests for dependency resolution order and fail-closed behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from donna.api.auth import dependencies as dep


def _request(host: str, headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
        cookies={},
    )


@pytest.mark.asyncio
async def test_resolve_user_device_token_short_circuits(monkeypatch):
    ctx = dep.AuthContext(
        conn=AsyncMock(),
        auth_config=None,
        immich_client=AsyncMock(),
    )

    async def fake_device_validate(*args, **kwargs):
        return {"user_id": "nick"}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)

    req = _request("203.0.113.5", {"authorization": "Bearer abc"})
    user_id = await dep._resolve_user_id(
        req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[]
    )
    assert user_id == "nick"
    ctx.immich_client.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_user_untrusted_ip_raises_403(monkeypatch):
    ctx = dep.AuthContext(conn=AsyncMock(), auth_config=None, immich_client=AsyncMock())

    async def fake_device_validate(*args, **kwargs):
        return None

    async def fake_ip_check(conn, ip, **kwargs):
        return {"action": "challenge", "reason": "unknown_ip", "ip_record": None}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)
    monkeypatch.setattr(dep.ip_gate, "check_ip_access", fake_ip_check)

    req = _request("203.0.113.5")
    with pytest.raises(HTTPException) as exc:
        await dep._resolve_user_id(
            req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[]
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_user_missing_immich_raises_401(monkeypatch):
    ctx = dep.AuthContext(conn=AsyncMock(), auth_config=None, immich_client=AsyncMock())

    async def fake_device_validate(*args, **kwargs):
        return None

    async def fake_ip_check(conn, ip, **kwargs):
        return {"action": "allow", "reason": "trusted", "ip_record": {"access_level": "user"}}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)
    monkeypatch.setattr(dep.ip_gate, "check_ip_access", fake_ip_check)

    req = _request("203.0.113.5")
    with pytest.raises(HTTPException) as exc:
        await dep._resolve_user_id(
            req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[]
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_admin_rejects_device_token_path(monkeypatch):
    ctx = dep.AuthContext(conn=AsyncMock(), auth_config=None, immich_client=AsyncMock())

    async def fake_device_validate(*args, **kwargs):
        return {"user_id": "nick"}

    async def fake_ip_check(conn, ip, **kwargs):
        return {"action": "challenge", "reason": "unknown_ip", "ip_record": None}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)
    monkeypatch.setattr(dep.ip_gate, "check_ip_access", fake_ip_check)

    req = _request("203.0.113.5", {"authorization": "Bearer abc"})
    with pytest.raises(HTTPException) as exc:
        await dep._resolve_admin_user_id(
            req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[],
        )
    assert exc.value.status_code in (401, 403)
