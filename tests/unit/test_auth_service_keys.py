"""Unit tests for per-caller LLM gateway service keys."""

from __future__ import annotations

import ipaddress

import aiosqlite
import pytest

from donna.api.auth import service_keys as sk


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "sk.db"
    async with aiosqlite.connect(str(path)) as conn:
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
        yield conn


_INTERNAL = [ipaddress.ip_network("172.18.0.0/16")]


@pytest.mark.asyncio
async def test_issue_seed_and_validate(db):
    raw = await sk.seed_or_rotate(
        db, caller_id="curator", monthly_budget_usd=5.0
    )
    result = await sk.validate(
        db, presented_key=raw, source_ip="172.18.0.5",
        internal_cidrs=_INTERNAL, forwarded_host=None,
    )
    assert result is not None
    assert result["caller_id"] == "curator"


@pytest.mark.asyncio
async def test_external_ip_rejected(db):
    raw = await sk.seed_or_rotate(db, caller_id="curator", monthly_budget_usd=5.0)
    result = await sk.validate(
        db, presented_key=raw, source_ip="8.8.8.8",
        internal_cidrs=_INTERNAL, forwarded_host=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_caddy_proxied_request_rejected(db):
    raw = await sk.seed_or_rotate(db, caller_id="curator", monthly_budget_usd=5.0)
    result = await sk.validate(
        db, presented_key=raw, source_ip="172.18.0.5",
        internal_cidrs=_INTERNAL, forwarded_host="donna.houseoffeuer.com",
    )
    assert result is None


@pytest.mark.asyncio
async def test_disabled_caller_rejected(db):
    raw = await sk.seed_or_rotate(db, caller_id="curator", monthly_budget_usd=5.0)
    await db.execute("UPDATE llm_gateway_callers SET enabled=0 WHERE caller_id='curator'")
    await db.commit()
    result = await sk.validate(
        db, presented_key=raw, source_ip="172.18.0.5",
        internal_cidrs=_INTERNAL, forwarded_host=None,
    )
    assert result is None
