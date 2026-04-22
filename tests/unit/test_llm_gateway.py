# tests/unit/test_llm_gateway.py — full replacement
"""Unit tests for the LLM gateway routes."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from donna.api.routes.llm import (
    llm_health,
    llm_models,
    llm_queue_item,
    llm_queue_status,
)
from donna.llm.types import GatewayConfig
from donna.logging.invocation_logger import InvocationMetadata
from donna.models.types import CompletionMetadata


def _make_request(
    *,
    ollama: AsyncMock | None = None,
    queue: MagicMock | None = None,
    gateway_config: GatewayConfig | None = None,
) -> MagicMock:
    request = MagicMock()
    request.app.state.ollama = ollama
    request.app.state.llm_queue = queue
    request.app.state.llm_gateway_config = gateway_config or GatewayConfig()
    conn = AsyncMock()
    conn.commit = AsyncMock()
    request.app.state.db.connection = conn
    return request


def _make_meta() -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=500, tokens_in=100, tokens_out=50,
        cost_usd=0.001, model_actual="ollama/test",
    )


def test_invocation_metadata_has_gateway_fields() -> None:
    meta = InvocationMetadata(
        task_type="external_llm_call",
        model_alias="gateway/test",
        model_actual="ollama/qwen",
        input_hash="abc123",
        latency_ms=500,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        user_id="gateway",
        queue_wait_ms=1200,
        interrupted=True,
        chain_id="chain-xyz",
        caller="immich-tagger",
    )
    assert meta.queue_wait_ms == 1200
    assert meta.interrupted is True
    assert meta.chain_id == "chain-xyz"
    assert meta.caller == "immich-tagger"


class TestLLMHealth:
    async def test_health_ok(self) -> None:
        ollama = AsyncMock()
        ollama.health = AsyncMock(return_value=True)
        request = _make_request(ollama=ollama)
        result = await llm_health(request)
        assert result["ok"] is True

    async def test_health_no_provider(self) -> None:
        request = _make_request(ollama=None)
        result = await llm_health(request)
        assert result["ok"] is False


class TestLLMModels:
    async def test_list_models(self) -> None:
        ollama = AsyncMock()
        ollama.list_models = AsyncMock(return_value=["model-a", "model-b"])
        request = _make_request(ollama=ollama)
        result = await llm_models(request)
        assert result["models"] == ["model-a", "model-b"]


class TestQueueStatus:
    async def test_returns_status(self) -> None:
        queue = MagicMock()
        queue.get_status.return_value = {
            "current_request": None,
            "internal_queue": {"pending": 0},
            "external_queue": {"pending": 0},
            "stats_24h": {},
            "rate_limits": {},
            "mode": "active",
        }
        request = _make_request(queue=queue)
        result = await llm_queue_status(request)
        assert result["mode"] == "active"


class TestQueueItem:
    async def test_returns_item_when_found(self) -> None:
        queue = MagicMock()
        queue.get_item.return_value = {
            "sequence": 1,
            "type": "external",
            "caller": "test",
            "model": "m",
            "enqueued_at": "2026-04-11T00:00:00+00:00",
            "prompt": "full prompt",
            "max_tokens": 100,
            "json_mode": True,
        }
        request = _make_request(queue=queue)
        result = await llm_queue_item(1, request)
        assert result["prompt"] == "full prompt"
        assert result["sequence"] == 1

    async def test_returns_404_when_not_found(self) -> None:
        queue = MagicMock()
        queue.get_item.return_value = None
        request = _make_request(queue=queue)
        with pytest.raises(HTTPException) as exc_info:
            await llm_queue_item(999, request)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Fail-closed auth tests for /llm/completions (service_router gate).
# ---------------------------------------------------------------------------


@pytest.fixture
async def llm_auth_app(tmp_path):
    """FastAPI app with /llm routes mounted and one seeded caller key."""
    import ipaddress

    import aiosqlite
    from fastapi import FastAPI

    from donna.api.auth import dependencies as auth_deps
    from donna.api.auth import service_keys
    from donna.api.auth.config import (
        AuthConfig,
        BootstrapSettings,
        DeviceTokenSettings,
        EmailSettings,
        ImmichSettings,
        IPGateConfig,
        RateLimit,
    )
    from donna.api.routes import llm as llm_routes

    db_path = tmp_path / "llm_gw.db"
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE llm_gateway_callers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id TEXT NOT NULL UNIQUE,
            key_hash TEXT NOT NULL,
            monthly_budget_usd REAL NOT NULL DEFAULT 0.0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            revoked_at DATETIME, revoke_reason TEXT
        );
        """
    )
    await conn.commit()
    raw_key = await service_keys.seed_or_rotate(
        conn, caller_id="homebox", monthly_budget_usd=5.0,
    )

    cfg = AuthConfig(
        ip_gate=IPGateConfig(
            default_trust_duration="30d",
            durations_allowed=["24h", "7d", "30d", "90d"],
            rate_limit_request_access=RateLimit(max=100, window_seconds=3600),
            rate_limit_verify=RateLimit(max=100, window_seconds=600),
        ),
        trusted_proxies=[ipaddress.ip_network("127.0.0.0/8")],
        internal_cidrs=[ipaddress.ip_network("172.18.0.0/16")],
        immich=ImmichSettings(
            internal_url="http://immich:2283",
            external_url="https://immich.example",
            admin_api_key_env="IMMICH_ADMIN_API_KEY",
            user_cache_ttl_seconds=60,
            allowlist_sync_interval_seconds=900,
            allowlist_stale_tolerance_seconds=86400,
        ),
        device_tokens=DeviceTokenSettings(
            sliding_window_days=90, absolute_max_days=365, max_per_user=10,
        ),
        email=EmailSettings(
            from_addr="donna@example",
            subject="Donna verify",
            verify_base_url="https://donna.example/auth/verify",
            token_expiry_minutes=15,
        ),
        bootstrap=BootstrapSettings(admin_email_env="DONNA_BOOTSTRAP_ADMIN_EMAIL"),
    )

    app = FastAPI()
    app.state.auth_config = cfg
    app.state.auth_context = auth_deps.AuthContext(
        conn=conn, auth_config=cfg, immich_client=None,
    )
    app.state.llm_queue = None
    app.state.rate_limiter = None
    app.state.llm_gateway_config = None
    app.state.models_config = None
    app.state.ollama = None
    app.include_router(llm_routes.router, prefix="/llm")

    yield app, raw_key
    await conn.close()


def _internal_transport(app):
    from httpx import ASGITransport
    return ASGITransport(app=app, client=("172.18.0.99", 40000))


def _external_transport(app):
    from httpx import ASGITransport
    return ASGITransport(app=app, client=("203.0.113.7", 40000))


@pytest.mark.asyncio
async def test_llm_completions_rejected_without_key(llm_auth_app):
    """Missing X-Donna-Service-Key must fail closed with 401."""
    from httpx import AsyncClient

    app, _ = llm_auth_app
    async with AsyncClient(transport=_internal_transport(app), base_url="http://t") as c:
        resp = await c.post("/llm/completions", json={"prompt": "hi"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "service_key_invalid"


@pytest.mark.asyncio
async def test_llm_completions_rejected_when_caddy_proxied(llm_auth_app):
    """Valid key but X-Forwarded-Host present (Caddy-proxied) → 401."""
    from httpx import AsyncClient

    app, raw_key = llm_auth_app
    async with AsyncClient(transport=_internal_transport(app), base_url="http://t") as c:
        resp = await c.post(
            "/llm/completions",
            json={"prompt": "hi"},
            headers={
                "x-donna-service-key": raw_key,
                "x-forwarded-host": "donna.houseoffeuer.com",
            },
        )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "service_key_invalid"


@pytest.mark.asyncio
async def test_llm_completions_rejected_from_external_ip(llm_auth_app):
    """Valid key but source IP outside internal CIDR → 401."""
    from httpx import AsyncClient

    app, raw_key = llm_auth_app
    async with AsyncClient(transport=_external_transport(app), base_url="http://t") as c:
        resp = await c.post(
            "/llm/completions",
            json={"prompt": "hi"},
            headers={"x-donna-service-key": raw_key},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_llm_completions_rejected_with_wrong_key(llm_auth_app):
    """Random non-matching key is rejected even from an internal IP."""
    from httpx import AsyncClient

    app, _ = llm_auth_app
    async with AsyncClient(transport=_internal_transport(app), base_url="http://t") as c:
        resp = await c.post(
            "/llm/completions",
            json={"prompt": "hi"},
            headers={"x-donna-service-key": "not-the-real-key"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_llm_completions_accepts_valid_internal_caller(llm_auth_app):
    """Valid key from internal CIDR with no X-Forwarded-Host passes the gate.

    The handler then returns 503 (no queue worker initialised) which
    proves authentication succeeded — otherwise the dependency would
    have short-circuited with 401 first.
    """
    from httpx import AsyncClient

    app, raw_key = llm_auth_app
    async with AsyncClient(transport=_internal_transport(app), base_url="http://t") as c:
        resp = await c.post(
            "/llm/completions",
            json={"prompt": "hi"},
            headers={"x-donna-service-key": raw_key},
        )
    assert resp.status_code == 503
