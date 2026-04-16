"""Unit tests for email allowlist sync and lookup."""

from __future__ import annotations

import aiosqlite
import pytest
from aioresponses import aioresponses

from donna.api.auth import email_allowlist as ea


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "ea.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE allowed_emails (
                email TEXT PRIMARY KEY,
                immich_user_id TEXT NOT NULL,
                name TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                synced_at DATETIME NOT NULL
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_sync_replaces_table(db):
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/admin/users",
            status=200,
            payload=[
                {"id": "u1", "email": "Nick@Example.com", "name": "Nick", "isAdmin": True},
                {"id": "u2", "email": "dad@example.com", "name": "Dad", "isAdmin": False},
            ],
        )
        await ea.sync(
            db,
            internal_url="http://immich:2283",
            admin_api_key="secret",
        )
    assert await ea.is_allowed(db, "nick@example.com")
    assert await ea.is_allowed(db, "dad@example.com")
    assert not await ea.is_allowed(db, "attacker@evil.com")


@pytest.mark.asyncio
async def test_normalization_strips_and_lowercases(db):
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/admin/users",
            status=200,
            payload=[{"id": "u1", "email": "Nick@Example.com", "name": "Nick", "isAdmin": False}],
        )
        await ea.sync(db, internal_url="http://immich:2283", admin_api_key="secret")
    assert await ea.is_allowed(db, "  NICK@example.com  ")
