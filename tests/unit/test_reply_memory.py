"""Tests for thread conversation memory."""
from __future__ import annotations

import aiosqlite
import pytest
import uuid6

from donna.replies.memory import ThreadMemory


@pytest.fixture
async def mem_db():
    """In-memory SQLite with thread_memory table."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE thread_memory (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            context_type TEXT NOT NULL,
            task_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at)"
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_record_and_retrieve(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=10)
    await mem.record("thread-1", "overdue", "t1", "donna", "You're overdue on Build thing.")
    await mem.record("thread-1", "overdue", "t1", "user", "done")
    messages = await mem.retrieve("thread-1")
    assert len(messages) == 2
    assert messages[0]["role"] == "donna"
    assert messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_retrieve_respects_window_size(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=3)
    for i in range(5):
        await mem.record("thread-1", "overdue", "t1", "user", f"msg-{i}")
    messages = await mem.retrieve("thread-1")
    assert len(messages) == 3
    assert messages[0]["content"] == "msg-2"
    assert messages[2]["content"] == "msg-4"


@pytest.mark.asyncio
async def test_retrieve_empty_thread(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=10)
    messages = await mem.retrieve("nonexistent")
    assert messages == []


@pytest.mark.asyncio
async def test_prune_old_messages(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=10)
    # Insert a message with an old timestamp
    await mem_db.execute(
        "INSERT INTO thread_memory (id, thread_id, context_type, task_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid6.uuid7()), "thread-1", "overdue", "t1", "user", "old msg", "2020-01-01T00:00:00+00:00"),
    )
    await mem_db.commit()
    # Insert a recent message
    await mem.record("thread-1", "overdue", "t1", "user", "new msg")
    pruned = await mem.prune(retention_days=7)
    assert pruned >= 1
    messages = await mem.retrieve("thread-1")
    assert len(messages) == 1
    assert messages[0]["content"] == "new msg"
