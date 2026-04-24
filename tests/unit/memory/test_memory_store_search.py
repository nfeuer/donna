"""MemoryStore.search — k-clamp, min_score filter, ordering."""
from __future__ import annotations

import pytest

from donna.config import VaultRetrievalConfig
from donna.memory.chunking import MarkdownHeadingChunker
from donna.memory.store import Document, MemoryStore


@pytest.mark.asyncio
async def test_k_is_clamped_to_max_k(memory_db, fake_provider) -> None:
    cfg = VaultRetrievalConfig(default_k=3, max_k=5, min_score=0.0)
    store = MemoryStore(memory_db, fake_provider, MarkdownHeadingChunker(), cfg)
    # Seed 8 docs, each with its own chunk.
    for i in range(8):
        await store.upsert(
            Document(
                user_id="nick",
                source_type="vault",
                source_id=f"Inbox/doc{i}.md",
                title=f"Doc {i}",
                uri=f"vault:Inbox/doc{i}.md",
                content=f"# D{i}\n\nContent alpha{i} beta gamma",
            )
        )
    hits = await store.search(query="alpha", user_id="nick", k=100)
    assert len(hits) <= cfg.max_k


@pytest.mark.asyncio
async def test_min_score_filters_low_quality(memory_db, fake_provider) -> None:
    # A `min_score` of 0.99 filters out essentially everything from
    # random-vector hits, proving the filter is applied.
    cfg = VaultRetrievalConfig(default_k=5, max_k=10, min_score=0.99)
    store = MemoryStore(memory_db, fake_provider, MarkdownHeadingChunker(), cfg)
    await store.upsert(
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Inbox/a.md",
            title="A",
            uri="vault:Inbox/a.md",
            content="# A\n\nalpha beta gamma",
        )
    )
    hits = await store.search(query="totally unrelated zebras", user_id="nick")
    assert hits == []


@pytest.mark.asyncio
async def test_results_sorted_by_score_desc(memory_db, fake_provider) -> None:
    cfg = VaultRetrievalConfig(default_k=10, max_k=20, min_score=0.0)
    store = MemoryStore(memory_db, fake_provider, MarkdownHeadingChunker(), cfg)
    for i in range(5):
        await store.upsert(
            Document(
                user_id="nick",
                source_type="vault",
                source_id=f"Inbox/d{i}.md",
                title=None,
                uri=None,
                content=f"# D\n\nunique content marker {i} here",
            )
        )
    hits = await store.search(query="unique content marker", user_id="nick")
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
