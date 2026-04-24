"""Slice 16 — :class:`PersonMentionCounter` SQL sweep + threshold filter."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from donna.capabilities.person_mention_counter import (
    PersonMentionCounter,
    names_above_threshold,
)
from donna.memory.store import Document, MemoryStore


@pytest.mark.asyncio
async def test_counts_bare_and_namespaced_together(
    memory_db: aiosqlite.Connection,
    memory_store: MemoryStore,
) -> None:
    """``[[Alice]]`` and ``[[People/Alice]]`` aggregate to the same name."""
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="chat",
            source_id="s1:m1",
            title="t",
            uri=None,
            content=(
                "chat about [[Alice]] and [[People/Alice]] again."
            ),
        )
    )
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Meetings/2026-04-22.md",
            title="m",
            uri=None,
            content="with [[Alice]] and [[Bob|B]] and [[Carol]]",
        )
    )

    counter = PersonMentionCounter(memory_db)
    counts = await counter.scan(user_id="nick", lookback_days=7)

    # Alice: 3 (1 bare + 1 namespaced in chat + 1 bare in meeting).
    # Bob: 1 aliased wikilink — still counts as a mention.
    # Carol: 1.
    assert counts.get("Alice") == 3
    assert counts.get("Bob") == 1
    assert counts.get("Carol") == 1


@pytest.mark.asyncio
async def test_lookback_window_excludes_old(
    memory_db: aiosqlite.Connection,
    memory_store: MemoryStore,
) -> None:
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="chat",
            source_id="s1:new",
            title="t",
            uri=None,
            content="meeting [[Alice]]",
        )
    )
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="chat",
            source_id="s1:old",
            title="t",
            uri=None,
            content="old chat [[Alice]]",
        )
    )
    # Push the 'old' row outside the window.
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? WHERE source_id='s1:old'",
        ((datetime.now(UTC) - timedelta(days=30)).replace(tzinfo=None).isoformat(),),
    )
    await memory_db.commit()

    counter = PersonMentionCounter(memory_db)
    counts = await counter.scan(user_id="nick", lookback_days=7)

    assert counts.get("Alice") == 1


def test_names_above_threshold_filters_and_orders() -> None:
    out = names_above_threshold(
        {"Alice": 5, "Bob": 3, "Carol": 2, "Dave": 3}, threshold=3
    )
    # Sorted desc by count; ties broken alphabetically.
    assert out == [("Alice", 5), ("Bob", 3), ("Dave", 3)]
