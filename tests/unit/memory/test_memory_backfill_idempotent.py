"""Unit test: chat/task/correction backfill is idempotent.

Running each source's ``backfill(user_id)`` twice must leave
``memory_documents`` + ``memory_chunks`` row counts unchanged. This is
enforced at the store level by ``UNIQUE(user_id, source_type,
source_id)``; the test asserts that the chosen ``source_id`` shape on
each source actually respects that invariant.
"""
from __future__ import annotations

import aiosqlite
import pytest

from donna.config import (
    ChatSourceConfig,
    CorrectionSourceConfig,
    TaskSourceConfig,
)
from donna.memory.sources_chat import ChatSource
from donna.memory.sources_correction import CorrectionSource
from donna.memory.sources_task import TaskSource
from donna.memory.store import MemoryStore


async def _seed(conn: aiosqlite.Connection) -> None:
    # Alembic has already created conversation_sessions /
    # conversation_messages / correction_log / tasks on this
    # connection; we just seed rows.
    await conn.execute(
        "INSERT INTO conversation_sessions "
        "(id, user_id, channel, status, created_at, last_activity, "
        " expires_at, message_count) "
        "VALUES ('sess-1','nick','discord','active','2026-04-01',"
        "        '2026-04-01','2026-04-02',4)"
    )
    for i, (role, content) in enumerate(
        [
            ("user", "Hi, let's plan the week."),
            ("user", "Any blockers on onboarding?"),
            ("assistant", "No blockers, here is the draft."),
            ("assistant", "Shipping tomorrow."),
        ],
        start=1,
    ):
        await conn.execute(
            "INSERT INTO conversation_messages "
            "(id, session_id, role, content, created_at) "
            "VALUES (?, 'sess-1', ?, ?, ?)",
            (f"m{i}", role, content, f"2026-04-01T12:0{i}:00"),
        )
    await conn.execute(
        "INSERT INTO tasks (id, user_id, title, description, domain, priority, "
        "status, estimated_duration, deadline, deadline_type, scheduled_start, "
        "actual_start, completed_at, recurrence, dependencies, parent_task, "
        "prep_work_flag, prep_work_instructions, agent_eligible, assigned_agent, "
        "agent_status, tags, notes, reschedule_count, created_at, created_via, "
        "estimated_cost, calendar_event_id, donna_managed, nudge_count, "
        "quality_score, capability_name, inputs_json) VALUES "
        "('task-1','nick','Draft onboarding','Plan week','work',3,'backlog',"
        "NULL,NULL,'none',NULL,NULL,NULL,NULL,NULL,NULL,0,NULL,0,NULL,NULL,"
        "NULL,NULL,0,'2026-04-01','discord',NULL,NULL,0,0,NULL,NULL,NULL)"
    )
    await conn.execute(
        "INSERT INTO correction_log "
        "(id, timestamp, user_id, task_type, task_id, input_text, "
        " field_corrected, original_value, corrected_value) VALUES "
        "('corr-1','2026-04-01','nick','classify_priority','task-1',"
        " 'urgent','priority','2','4')"
    )
    await conn.commit()


async def _count(conn: aiosqlite.Connection, table: str) -> int:
    async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        row = await cur.fetchone()
    return int(row[0])


@pytest.mark.asyncio
async def test_backfill_twice_is_idempotent(memory_store: MemoryStore) -> None:
    conn = memory_store._conn  # type: ignore[attr-defined]
    await _seed(conn)
    chat = ChatSource(
        store=memory_store, cfg=ChatSourceConfig(enabled=True, min_chars=1),
    )
    task = TaskSource(
        store=memory_store, cfg=TaskSourceConfig(enabled=True),
    )
    corr = CorrectionSource(
        store=memory_store, cfg=CorrectionSourceConfig(enabled=True),
    )

    async def run_all() -> None:
        await chat.backfill("nick")
        await task.backfill("nick")
        await corr.backfill("nick")

    await run_all()
    docs_after_first = await _count(conn, "memory_documents")
    chunks_after_first = await _count(conn, "memory_chunks")
    await run_all()
    docs_after_second = await _count(conn, "memory_documents")
    chunks_after_second = await _count(conn, "memory_chunks")
    assert docs_after_first == docs_after_second
    assert chunks_after_first == chunks_after_second
    # Sanity: we actually indexed something.
    assert docs_after_first >= 3
