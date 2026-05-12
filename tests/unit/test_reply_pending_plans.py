"""Tests for pending action plan persistence."""
from __future__ import annotations

import json

import aiosqlite
import pytest

from donna.replies.pending_plans import PendingPlans


@pytest.fixture
async def plan_db():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE pending_action_plan (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX idx_pending_plan_thread ON pending_action_plan(thread_id, status)"
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_save_and_get_pending(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    actions = [{"action": "mark_done", "params": {"task_id": "t1"}}]
    plan_id = await plans.save("thread-1", actions, "I'll mark it done. Go ahead?")
    pending = await plans.get_pending("thread-1")
    assert pending is not None
    assert pending["id"] == plan_id
    assert pending["status"] == "pending"
    assert json.loads(pending["actions_json"]) == actions


@pytest.mark.asyncio
async def test_confirm_plan(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    actions = [{"action": "mark_done", "params": {"task_id": "t1"}}]
    plan_id = await plans.save("thread-1", actions, "reply")
    result = await plans.confirm("thread-1")
    assert result is not None
    assert json.loads(result["actions_json"]) == actions
    # After confirmation, no pending plan
    assert await plans.get_pending("thread-1") is None


@pytest.mark.asyncio
async def test_reject_plan(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    await plans.save("thread-1", [{"action": "reschedule", "params": {}}], "reply")
    await plans.reject("thread-1")
    assert await plans.get_pending("thread-1") is None


@pytest.mark.asyncio
async def test_expire_old_plans(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=0)
    await plans.save("thread-1", [{"action": "snooze", "params": {}}], "reply")
    expired = await plans.expire_stale()
    assert expired >= 1
    assert await plans.get_pending("thread-1") is None


@pytest.mark.asyncio
async def test_no_pending_returns_none(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    assert await plans.get_pending("nonexistent") is None
