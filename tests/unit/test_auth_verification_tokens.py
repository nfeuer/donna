"""Unit tests for magic-link verification tokens."""

from __future__ import annotations

import aiosqlite
import pytest

from donna.api.auth import verification_tokens as vt


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "vt.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE verification_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                ip_address TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                trust_duration TEXT NOT NULL DEFAULT '30d'
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_create_and_validate_round_trip(db):
    raw = await vt.create(db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=15)
    assert len(raw) >= 32
    record = await vt.validate(db, token=raw, ip="1.2.3.4")
    assert record is not None
    assert record["email"] == "nick@example.com"


@pytest.mark.asyncio
async def test_validation_rejects_wrong_ip(db):
    raw = await vt.create(db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=15)
    record = await vt.validate(db, token=raw, ip="9.9.9.9")
    assert record is None


@pytest.mark.asyncio
async def test_mark_used_prevents_replay(db):
    raw = await vt.create(db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=15)
    await vt.mark_used(db, token=raw)
    record = await vt.validate(db, token=raw, ip="1.2.3.4")
    assert record is None


@pytest.mark.asyncio
async def test_expired_token_rejected(db):
    raw = await vt.create(
        db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=-1
    )
    record = await vt.validate(db, token=raw, ip="1.2.3.4")
    assert record is None


@pytest.mark.asyncio
async def test_sql_injection_email_is_literal(db):
    payload = "x@example.com'; DROP TABLE verification_tokens; --"
    await vt.create(db, ip="1.2.3.4", email=payload, expiry_minutes=15)
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    assert any(r[0] == "verification_tokens" for r in rows)
