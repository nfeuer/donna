"""Unit tests for device token issuance, validation, sliding window."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite
import pytest

from donna.api.auth import device_tokens as dt


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "dt.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE device_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                token_lookup TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                label TEXT, user_agent TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME, last_seen_ip TEXT,
                expires_at DATETIME NOT NULL,
                revoked_at DATETIME, revoked_by TEXT
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_issue_and_validate(db):
    raw = await dt.issue(
        db, user_id="nick", label="iPhone", user_agent="ua", ip="1.2.3.4",
        sliding_window_days=90, absolute_max_days=365,
    )
    assert len(raw) >= 32
    record = await dt.validate(db, token=raw, ip="2.2.2.2", sliding_window_days=90, absolute_max_days=365)
    assert record is not None
    assert record["user_id"] == "nick"


@pytest.mark.asyncio
async def test_revoked_token_rejected(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    row = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    await dt.revoke(db, device_id=row["id"], revoked_by="admin")
    record = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    assert record is None


@pytest.mark.asyncio
async def test_expired_token_rejected(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    await db.execute(
        "UPDATE device_tokens SET expires_at=? WHERE user_id='nick'",
        ((datetime.utcnow() - timedelta(days=1)).isoformat(),),
    )
    await db.commit()
    record = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    assert record is None


@pytest.mark.asyncio
async def test_sliding_window_extends_expires(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    await db.execute(
        "UPDATE device_tokens SET expires_at=? WHERE user_id='nick'",
        ((datetime.utcnow() + timedelta(days=10)).isoformat(),),
    )
    await db.commit()
    await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    cursor = await db.execute("SELECT expires_at FROM device_tokens WHERE user_id='nick'")
    row = await cursor.fetchone()
    new_exp = datetime.fromisoformat(row[0])
    assert new_exp > datetime.utcnow() + timedelta(days=80)


@pytest.mark.asyncio
async def test_absolute_max_caps_sliding_window(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    await db.execute(
        "UPDATE device_tokens SET created_at=? WHERE user_id='nick'",
        ((datetime.utcnow() - timedelta(days=360)).isoformat(),),
    )
    await db.commit()
    record = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    assert record is not None
    cursor = await db.execute("SELECT expires_at FROM device_tokens WHERE user_id='nick'")
    row = await cursor.fetchone()
    new_exp = datetime.fromisoformat(row[0])
    assert new_exp <= datetime.utcnow() + timedelta(days=6)


@pytest.mark.asyncio
async def test_sql_injection_label_is_literal(db):
    payload = "iPhone'; DROP TABLE device_tokens; --"
    await dt.issue(db, user_id="nick", label=payload, user_agent="ua", ip="1.2.3.4",
                    sliding_window_days=90, absolute_max_days=365)
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    assert any(r[0] == "device_tokens" for r in rows)
