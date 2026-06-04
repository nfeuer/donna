"""Regression: concurrent writes on the shared aiosqlite connection.

The orchestrator shares one aiosqlite connection between the task
``Database`` and the ``MemoryStore`` (``MemoryStore(db.connection, ...)``).
Memory upserts compute embeddings at an ``await`` point *before* opening
an explicit ``BEGIN`` transaction. When two writers interleave on that
shared connection, one writer's explicit ``BEGIN`` used to land while
another transaction was already open, raising sqlite's
``cannot start a transaction within a transaction`` — surfaced in
production as ``memory_ingest_failed``.

These tests pin the serialization contract that prevents it.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from donna.config import VaultRetrievalConfig
from donna.memory.chunking import MarkdownHeadingChunker
from donna.memory.store import Document, MemoryStore

from .conftest import FakeEmbeddingProvider


class YieldingProvider(FakeEmbeddingProvider):
    """Embedding provider that yields control mid-embed.

    The yield forces the asyncio scheduler to interleave concurrent
    upserts exactly the way the real (network-bound) provider does,
    reproducing the shared-connection transaction race deterministically.
    """

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None,
    ) -> list[np.ndarray]:
        await asyncio.sleep(0)
        return await super().embed_batch(texts, task_type=task_type)


def _doc(i: int) -> Document:
    return Document(
        user_id="nick",
        source_type="task",
        source_id=f"task-{i}",
        title=f"Task {i}",
        uri=f"task:task-{i}",
        content=f"# Task {i}\n\nDo the thing number {i} with care and rigor.",
    )


@pytest.mark.asyncio
async def test_concurrent_upserts_do_not_collide(memory_db) -> None:
    """Many concurrent upserts on one connection must all commit cleanly."""
    store = MemoryStore(
        memory_db,
        YieldingProvider(),
        MarkdownHeadingChunker(),
        VaultRetrievalConfig(default_k=5, max_k=10, min_score=0.0),
    )

    # Without serialization, at least one of these raises
    # "cannot start a transaction within a transaction".
    await asyncio.gather(*(store.upsert(_doc(i)) for i in range(8)))

    hits = await store.search(query="thing number", user_id="nick", k=100)
    assert len({h.source_id for h in hits}) == 8


@pytest.mark.asyncio
async def test_concurrent_upsert_and_upsert_many(memory_db) -> None:
    """The single-doc and batched paths must serialize against each other."""
    store = MemoryStore(
        memory_db,
        YieldingProvider(),
        MarkdownHeadingChunker(),
        VaultRetrievalConfig(default_k=5, max_k=20, min_score=0.0),
    )

    await asyncio.gather(
        store.upsert(_doc(0)),
        store.upsert_many([_doc(1), _doc(2), _doc(3)]),
        store.upsert(_doc(4)),
        store.upsert_many([_doc(5), _doc(6)]),
    )

    hits = await store.search(query="thing number", user_id="nick", k=100)
    assert len({h.source_id for h in hits}) == 7
