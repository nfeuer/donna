"""Slice 16 — :meth:`MemoryStore.rename` updates source_id without re-embed."""
from __future__ import annotations

import aiosqlite
import pytest

from donna.memory.store import Document, MemoryStore


@pytest.mark.asyncio
async def test_rename_updates_source_id_and_uri(
    memory_db: aiosqlite.Connection,
    memory_store: MemoryStore,
) -> None:
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Inbox/foo.md",
            title="Foo",
            uri="vault:Inbox/foo.md",
            content="# Foo\n\nhello world.\n",
        )
    )

    # Count chunks before the rename.
    async with memory_db.execute(
        "SELECT COUNT(*) FROM memory_chunks c "
        "JOIN memory_documents d ON d.id = c.document_id "
        "WHERE d.source_id='Inbox/foo.md'"
    ) as cur:
        chunks_before = (await cur.fetchone())[0]
    assert chunks_before > 0

    ok = await memory_store.rename(
        source_type="vault",
        old_source_id="Inbox/foo.md",
        new_source_id="Projects/foo.md",
        user_id="nick",
    )
    assert ok is True

    # Source moved; chunk count is identical (no re-embed).
    async with memory_db.execute(
        "SELECT source_id, uri FROM memory_documents WHERE source_id='Projects/foo.md'"
    ) as cur:
        row = await cur.fetchone()
    assert row == ("Projects/foo.md", "vault:Projects/foo.md")

    async with memory_db.execute(
        "SELECT COUNT(*) FROM memory_chunks c "
        "JOIN memory_documents d ON d.id = c.document_id "
        "WHERE d.source_id='Projects/foo.md'"
    ) as cur:
        chunks_after = (await cur.fetchone())[0]
    assert chunks_after == chunks_before


@pytest.mark.asyncio
async def test_rename_returns_false_on_missing_source(
    memory_store: MemoryStore,
) -> None:
    assert (
        await memory_store.rename(
            source_type="vault",
            old_source_id="nope.md",
            new_source_id="x.md",
            user_id="nick",
        )
        is False
    )


@pytest.mark.asyncio
async def test_rename_returns_false_on_target_collision(
    memory_store: MemoryStore,
) -> None:
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="vault",
            source_id="a.md",
            title="a",
            uri="vault:a.md",
            content="A body",
        )
    )
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="vault",
            source_id="b.md",
            title="b",
            uri="vault:b.md",
            content="B body",
        )
    )
    assert (
        await memory_store.rename(
            source_type="vault",
            old_source_id="a.md",
            new_source_id="b.md",
            user_id="nick",
        )
        is False
    )
