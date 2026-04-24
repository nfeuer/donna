"""Unit tests for :class:`donna.memory.sources_correction.CorrectionSource`."""
from __future__ import annotations

import pytest

from donna.config import CorrectionSourceConfig
from donna.memory.sources_correction import SOURCE_TYPE, CorrectionSource
from donna.memory.store import MemoryStore


def _event(**overrides: object) -> dict[str, object]:
    base = {
        "id": "c1",
        "user_id": "nick",
        "task_type": "classify_priority",
        "task_id": "t-1",
        "input_text": "urgent email",
        "field_corrected": "priority",
        "original_value": "2",
        "corrected_value": "4",
    }
    base.update(overrides)
    return base


async def _fetch_chunk_content(store: MemoryStore) -> list[str]:
    conn = store._conn  # type: ignore[attr-defined]
    async with conn.execute(
        "SELECT c.content FROM memory_chunks c "
        "JOIN memory_documents d ON d.id = c.document_id "
        "WHERE d.source_type=? AND d.deleted_at IS NULL "
        "ORDER BY c.chunk_index",
        (SOURCE_TYPE,),
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows]


@pytest.mark.asyncio
async def test_template_renders_all_fields(memory_store: MemoryStore) -> None:
    source = CorrectionSource(
        store=memory_store, cfg=CorrectionSourceConfig(enabled=True),
    )
    await source.observe(_event())
    chunks = await _fetch_chunk_content(memory_store)
    assert len(chunks) == 1
    body = chunks[0]
    assert "priority" in body
    assert "'2'" in body and "'4'" in body
    assert "urgent email" in body
    assert "classify_priority" in body


@pytest.mark.asyncio
async def test_idempotent_upsert_same_correction_id(
    memory_store: MemoryStore,
) -> None:
    source = CorrectionSource(
        store=memory_store, cfg=CorrectionSourceConfig(enabled=True),
    )
    await source.observe(_event())
    await source.observe(_event())  # same id — upsert, no duplicate
    conn = memory_store._conn  # type: ignore[attr-defined]
    async with conn.execute(
        "SELECT COUNT(*) FROM memory_documents "
        "WHERE source_type=? AND source_id=?",
        (SOURCE_TYPE, "c1"),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


@pytest.mark.asyncio
async def test_disabled_source_is_noop(memory_store: MemoryStore) -> None:
    source = CorrectionSource(
        store=memory_store, cfg=CorrectionSourceConfig(enabled=False),
    )
    await source.observe(_event())
    chunks = await _fetch_chunk_content(memory_store)
    assert chunks == []
