"""Unit tests for :class:`donna.memory.sources_chat.ChatSource`.

Focus: the emit-on-boundary contract (role flip, token cap, session
close) and idempotent source_id wiring.
"""
from __future__ import annotations

import pytest

from donna.config import ChatSourceConfig
from donna.memory.sources_chat import SOURCE_TYPE, ChatSource
from donna.memory.store import MemoryStore


async def _count_docs(store: MemoryStore) -> int:
    conn = store._conn  # type: ignore[attr-defined]
    async with conn.execute(
        "SELECT COUNT(*) FROM memory_documents WHERE source_type=?",
        (SOURCE_TYPE,),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0])


async def _fetch_source_ids(store: MemoryStore) -> list[str]:
    conn = store._conn  # type: ignore[attr-defined]
    async with conn.execute(
        "SELECT source_id FROM memory_documents "
        "WHERE source_type=? AND deleted_at IS NULL",
        (SOURCE_TYPE,),
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_role_flip_emits_turn(memory_store: MemoryStore) -> None:
    cfg = ChatSourceConfig(enabled=True, min_chars=1)
    source = ChatSource(store=memory_store, cfg=cfg)
    base = {"session_id": "S1", "user_id": "nick"}
    for mid, role, content in [
        ("m1", "user", "Hello there, any updates?"),
        ("m2", "user", "Follow-up question on onboarding."),
        # Role flip — should flush the prior buffer.
        ("m3", "assistant", "Yes, here is the summary."),
    ]:
        await source.observe_message(
            {**base, "message": {"id": mid, "role": role, "content": content}}
        )
    ids = await _fetch_source_ids(memory_store)
    assert len(ids) == 1, f"expected one flushed turn, got {ids}"
    assert ids[0] == "S1:m1-m2"


@pytest.mark.asyncio
async def test_session_close_flushes_pending_buffer(
    memory_store: MemoryStore,
) -> None:
    cfg = ChatSourceConfig(enabled=True, min_chars=1)
    source = ChatSource(store=memory_store, cfg=cfg)
    base = {"session_id": "S2", "user_id": "nick"}
    await source.observe_message(
        {**base, "message": {"id": "a", "role": "user", "content": "alpha beta gamma"}}
    )
    assert await _count_docs(memory_store) == 0
    await source.observe_session_closed({**base, "status": "closed"})
    ids = await _fetch_source_ids(memory_store)
    assert ids == ["S2:a-a"]


@pytest.mark.asyncio
async def test_idempotent_reupsert_on_same_buffer(
    memory_store: MemoryStore,
) -> None:
    cfg = ChatSourceConfig(enabled=True, min_chars=1)
    source = ChatSource(store=memory_store, cfg=cfg)
    base = {"session_id": "S3", "user_id": "nick"}
    await source.observe_message(
        {**base, "message": {"id": "m1", "role": "user", "content": "one two three"}}
    )
    await source.observe_session_closed({**base, "status": "closed"})
    first_ids = await _fetch_source_ids(memory_store)
    # Re-run backfill — row count must not grow.
    await source.backfill("nick")
    second_ids = await _fetch_source_ids(memory_store)
    assert first_ids == second_ids == ["S3:m1-m1"] or first_ids == second_ids


@pytest.mark.asyncio
async def test_disabled_source_does_nothing(memory_store: MemoryStore) -> None:
    cfg = ChatSourceConfig(enabled=False)
    source = ChatSource(store=memory_store, cfg=cfg)
    await source.observe_message(
        {
            "session_id": "S4",
            "user_id": "nick",
            "message": {"id": "x", "role": "user", "content": "should be ignored"},
        }
    )
    await source.observe_session_closed(
        {"session_id": "S4", "user_id": "nick", "status": "closed"}
    )
    assert await _count_docs(memory_store) == 0
