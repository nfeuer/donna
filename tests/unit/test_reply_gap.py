"""Tests for capability gap tracking."""
from __future__ import annotations

import aiosqlite
import pytest

from donna.replies.actions.gap_actions import CapabilityGapTracker


@pytest.fixture
async def gap_db():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
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
    """)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_log_new_gap(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book a restaurant", "User wants to book a restaurant", "overdue", "t1")
    cursor = await gap_db.execute("SELECT COUNT(*) FROM capability_gap")
    assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_dedup_increments_hit_count(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book a restaurant", "user wants to book a restaurant", "overdue", "t1")
    await tracker.log_gap("book restaurant please", "user wants to book a restaurant", "chat", "t2")
    cursor = await gap_db.execute("SELECT hit_count FROM capability_gap")
    row = await cursor.fetchone()
    assert row[0] == 2


@pytest.mark.asyncio
async def test_different_gaps_not_deduped(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book a restaurant", "book restaurant", "overdue", "t1")
    await tracker.log_gap("send a fax", "send fax to office", "chat", "t2")
    cursor = await gap_db.execute("SELECT COUNT(*) FROM capability_gap")
    assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_get_promotable_gaps(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book restaurant", "book restaurant", "overdue", None)
    await tracker.log_gap("book restaurant again", "book restaurant", "chat", None)
    await tracker.log_gap("book restaurant third", "book restaurant", "chat", None)
    promotable = await tracker.get_promotable(min_hits=3)
    assert len(promotable) == 1
    assert promotable[0]["hit_count"] >= 3
