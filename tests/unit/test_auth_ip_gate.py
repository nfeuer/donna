"""Unit tests for the async IP gate module."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite
import pytest

from donna.api.auth import ip_gate


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "ipgate.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE trusted_ips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                access_level TEXT,
                trust_duration TEXT,
                trusted_at DATETIME,
                expires_at DATETIME,
                verified_by TEXT,
                label TEXT,
                last_seen DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'web',
                revoked_at DATETIME,
                revoked_by TEXT,
                revoke_reason TEXT
            );
            CREATE TABLE ip_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                service TEXT, action TEXT, user_id TEXT
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_unknown_ip_is_challenge(db):
    result = await ip_gate.check_ip_access(db, "203.0.113.5")
    assert result["action"] == "challenge"
    assert result["reason"] == "unknown_ip"


@pytest.mark.asyncio
async def test_trust_ip_then_allow(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.6")
    await ip_gate.trust_ip(
        db, "203.0.113.6",
        access_level="user",
        trust_duration="30d",
        verified_by="test@example.com",
    )
    result = await ip_gate.check_ip_access(db, "203.0.113.6")
    assert result["action"] == "allow"
    assert result["ip_record"]["access_level"] == "user"


@pytest.mark.asyncio
async def test_expired_trust_returns_challenge(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.7")
    await db.execute(
        """UPDATE trusted_ips SET status='trusted', access_level='user',
                  trust_duration='24h',
                  trusted_at=?, expires_at=?
           WHERE ip_address=?""",
        (
            (datetime.utcnow() - timedelta(days=2)).isoformat(),
            (datetime.utcnow() - timedelta(days=1)).isoformat(),
            "203.0.113.7",
        ),
    )
    await db.commit()
    result = await ip_gate.check_ip_access(db, "203.0.113.7")
    assert result["action"] == "challenge"
    assert result["reason"] == "expired"


@pytest.mark.asyncio
async def test_revoked_is_block(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.8")
    await ip_gate.revoke_ip(db, "203.0.113.8", revoked_by="admin", reason="test")
    result = await ip_gate.check_ip_access(db, "203.0.113.8")
    assert result["action"] == "block"
    assert result["reason"] == "revoked"


@pytest.mark.asyncio
async def test_sql_injection_in_ip_is_literal(db):
    """Adversarial: ensure ip_address is treated as a literal string."""
    payload = "1.2.3.4'; DROP TABLE trusted_ips; --"
    await ip_gate.insert_pending_ip(db, payload)
    rows = await db.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    assert any(r[0] == "trusted_ips" for r in rows)


@pytest.mark.asyncio
async def test_admin_access_level_enforced_for_admin_service(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.9")
    await ip_gate.trust_ip(
        db, "203.0.113.9",
        access_level="user",
        trust_duration="30d",
        verified_by="x@example.com",
    )
    result = await ip_gate.check_ip_access(db, "203.0.113.9", service="admin")
    assert result["action"] == "block"
    assert result["reason"] == "insufficient_access_level"
