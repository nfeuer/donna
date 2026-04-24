"""Shared fixtures for slice-13 memory unit tests.

`FakeEmbeddingProvider` is deterministic — its vector is a function of
``hash(text)`` — so tests exercise the full MemoryStore pipeline
without paying a 3-second MiniLM load per test. The real provider is
exercised by the `@pytest.mark.slow` integration tests.
"""
from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import sqlite_vec

from donna.config import VaultRetrievalConfig
from donna.memory.chunking import MarkdownHeadingChunker
from donna.memory.store import MemoryStore


class FakeEmbeddingProvider:
    """Deterministic hash-seeded embedding provider for tests."""

    name = "fake"
    version_tag = "fake@v1"
    dim = 384
    max_tokens = 256

    def __init__(self) -> None:
        self.embed_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    async def embed(self, text: str) -> np.ndarray:
        self.embed_calls.append(text)
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        self.batch_calls.append(list(texts))
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(self.dim).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    @property
    def total_embed_rows(self) -> int:
        """Count of every row sent through `embed` or `embed_batch`."""
        return len(self.embed_calls) + sum(len(b) for b in self.batch_calls)


async def _open_memory_db() -> tuple[aiosqlite.Connection, Path]:
    """Open a fresh aiosqlite connection with vec0 loaded and the slice-13 schema."""
    tmp = Path(tempfile.mkstemp(prefix="donna_mem_", suffix=".db")[1])
    tmp.unlink(missing_ok=True)
    # Run alembic against the fresh file.
    from alembic.config import Config as AlembicConfig

    from alembic import command

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp}")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    conn = await aiosqlite.connect(str(tmp))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    raw = conn._conn
    await conn._execute(raw.enable_load_extension, True)
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())
    return conn, tmp


@pytest_asyncio.fixture
async def memory_db() -> AsyncIterator[aiosqlite.Connection]:
    conn, path = await _open_memory_db()
    try:
        yield conn
    finally:
        await conn.close()
        path.unlink(missing_ok=True)


@pytest.fixture
def fake_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def chunker() -> MarkdownHeadingChunker:
    return MarkdownHeadingChunker(max_tokens=60, overlap_tokens=8, min_tokens=5)


@pytest.fixture
def retrieval_cfg() -> VaultRetrievalConfig:
    return VaultRetrievalConfig(default_k=5, min_score=0.0, max_k=10)


@pytest_asyncio.fixture
async def memory_store(
    memory_db: aiosqlite.Connection,
    fake_provider: FakeEmbeddingProvider,
    chunker: MarkdownHeadingChunker,
    retrieval_cfg: VaultRetrievalConfig,
) -> MemoryStore:
    return MemoryStore(memory_db, fake_provider, chunker, retrieval_cfg)
