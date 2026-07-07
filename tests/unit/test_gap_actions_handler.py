"""Tests for the log_capability_gap handler glue (design item 9a).

Verifies that the module-level ``log_capability_gap`` function in
``donna.replies.actions.gap_actions`` is callable via ``getattr`` (the path
the reply handler uses) and correctly delegates to ``CapabilityGapTracker``
so that a gap row is persisted and a confirmation string is returned.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

import donna.replies.actions.gap_actions as gap_actions_mod
from donna.replies.actions.gap_actions import log_capability_gap

_CREATE_CAPABILITY_GAP = """
    CREATE TABLE capability_gap (
        id TEXT PRIMARY KEY,
        user_request TEXT NOT NULL,
        description TEXT NOT NULL,
        context_type TEXT,
        task_id TEXT,
        hit_count INTEGER DEFAULT 1,
        status TEXT DEFAULT 'logged',
        created_at TEXT NOT NULL,
        last_hit_at TEXT NOT NULL
    )
"""


@pytest.fixture
async def gap_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """In-memory sqlite connection seeded with capability_gap table."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(_CREATE_CAPABILITY_GAP)
    await conn.commit()
    yield conn
    await conn.close()


def _make_db(conn: aiosqlite.Connection) -> Any:
    """Minimal db stub that exposes .connection, matching Database.connection."""
    return SimpleNamespace(connection=conn)


@pytest.mark.asyncio
async def test_log_capability_gap_is_callable_via_getattr() -> None:
    """getattr lookup must not raise AttributeError (the bug this fixes)."""
    fn = gap_actions_mod.log_capability_gap
    assert callable(fn), "log_capability_gap must be a callable on the module"


@pytest.mark.asyncio
async def test_log_capability_gap_writes_row(gap_db: aiosqlite.Connection) -> None:
    """Calling the handler should insert a capability_gap row."""
    db = _make_db(gap_db)
    context: dict[str, Any] = {"task_id": "task-99"}
    params: dict[str, Any] = {
        "user_request": "Book a restaurant for Friday",
        "description": "User wants restaurant reservations",
        "context_type": "overdue",
    }

    result = await log_capability_gap(db, context, params)

    # Returns a non-empty confirmation string
    assert isinstance(result, str)
    assert len(result) > 0

    cursor = await gap_db.execute("SELECT COUNT(*) FROM capability_gap")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1, "exactly one gap row should be inserted"


@pytest.mark.asyncio
async def test_log_capability_gap_row_content(gap_db: aiosqlite.Connection) -> None:
    """Persisted row should carry correct user_request, description, task_id."""
    db = _make_db(gap_db)
    context: dict[str, Any] = {"task_id": "t-42"}
    params: dict[str, Any] = {
        "user_request": "send a fax",
        "description": "User wants to send a fax",
    }

    await log_capability_gap(db, context, params)

    cursor = await gap_db.execute(
        "SELECT user_request, description, task_id FROM capability_gap"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "send a fax"
    assert row[1] == "User wants to send a fax"
    assert row[2] == "t-42"  # task_id falls back to context["task_id"]


@pytest.mark.asyncio
async def test_log_capability_gap_returns_confirmation_string(
    gap_db: aiosqlite.Connection,
) -> None:
    """Return value must mention the description (first 60 chars)."""
    db = _make_db(gap_db)
    context: dict[str, Any] = {}
    params: dict[str, Any] = {
        "user_request": "order pizza",
        "description": "User wants pizza delivery",
    }

    result = await log_capability_gap(db, context, params)

    assert "User wants pizza delivery" in result


@pytest.mark.asyncio
async def test_log_capability_gap_deduplicates(gap_db: aiosqlite.Connection) -> None:
    """Calling handler twice with a similar description must deduplicate."""
    db = _make_db(gap_db)
    context: dict[str, Any] = {}
    params1: dict[str, Any] = {
        "user_request": "book a restaurant",
        "description": "User wants to book a restaurant",
    }
    params2: dict[str, Any] = {
        "user_request": "make a restaurant reservation",
        "description": "user wants to book a restaurant",  # near-dupe
    }

    await log_capability_gap(db, context, params1)
    await log_capability_gap(db, context, params2)

    cursor = await gap_db.execute("SELECT hit_count FROM capability_gap")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 2, "deduplication should increment hit_count rather than insert a new row"
