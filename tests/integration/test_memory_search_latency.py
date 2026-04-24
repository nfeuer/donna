"""Retrieval latency benchmark for MemoryStore.search.

Seeds 10,000 hash-embedded chunks via the FakeEmbeddingProvider, then
asserts p50 retrieval under 100 ms. p95 is printed for visibility but
is not enforced — CI machines vary.
"""
from __future__ import annotations

import asyncio
import statistics
import tempfile
import time
from pathlib import Path

import aiosqlite
import numpy as np
import pytest
import sqlite_vec


class FakeProvider:
    name = "fake"
    version_tag = "fake@v1"
    dim = 384
    max_tokens = 256

    async def embed(self, text: str) -> np.ndarray:
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


def _seed(db_path: Path) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


@pytest.mark.integration
def test_memory_search_latency_p50_under_100ms() -> None:
    tmp = Path(tempfile.mkstemp(prefix="donna_perf_", suffix=".db")[1])
    tmp.unlink(missing_ok=True)
    _seed(tmp)

    async def run() -> tuple[float, float]:
        conn = await aiosqlite.connect(str(tmp))
        try:
            raw = conn._conn
            await conn._execute(raw.enable_load_extension, True)
            await conn._execute(raw.load_extension, sqlite_vec.loadable_path())
            provider = FakeProvider()

            # Bulk insert 10,000 chunks — one per synthetic doc for
            # simplicity. We skip the chunker + MemoryStore write path
            # here because the benchmark is about retrieval, not
            # ingest; seeding via MemoryStore at this scale would add
            # minutes to the test.
            import json
            import uuid
            from datetime import datetime

            now = datetime.utcnow()
            doc_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO memory_documents "
                "(id, user_id, source_type, source_id, title, uri, "
                " content_hash, created_at, updated_at, deleted_at, "
                " sensitive, metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,0,NULL)",
                (doc_id, "nick", "vault", "bench.md", "Bench",
                 "vault:bench.md", "h", now, now),
            )
            for i in range(10_000):
                vec = provider._vec(f"chunk {i}")
                cid = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO memory_chunks "
                    "(chunk_id, document_id, user_id, chunk_index, content, "
                    " content_hash, heading_path, token_count, "
                    " embedding_version, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cid, doc_id, "nick", i, f"chunk {i}", "h",
                     json.dumps(["Bench"]), 3, provider.version_tag, now),
                )
                await conn.execute(
                    "INSERT INTO vec_memory_chunks (chunk_id, embedding) VALUES (?, ?)",
                    (cid, np.asarray(vec, dtype=np.float32).tobytes()),
                )
                if i % 1000 == 0:
                    await conn.commit()
            await conn.commit()

            # Build MemoryStore against the populated DB and time
            # `search`. Each call embeds the query via FakeProvider
            # and runs the sqlite-vec join.
            from donna.config import VaultRetrievalConfig
            from donna.memory.chunking import MarkdownHeadingChunker
            from donna.memory.store import MemoryStore

            store = MemoryStore(
                conn, provider,
                MarkdownHeadingChunker(),
                VaultRetrievalConfig(default_k=8, max_k=32, min_score=0.0),
            )
            latencies: list[float] = []
            for i in range(50):
                t0 = time.monotonic()
                await store.search(
                    query=f"query for chunk {i}", user_id="nick", k=8,
                )
                latencies.append((time.monotonic() - t0) * 1000)
            p50 = statistics.median(latencies)
            p95 = sorted(latencies)[int(len(latencies) * 0.95)]
            return p50, p95
        finally:
            await conn.close()

    p50, p95 = asyncio.run(run())
    tmp.unlink(missing_ok=True)
    print(f"\nmemory_search p50={p50:.1f}ms p95={p95:.1f}ms (N=50, 10k chunks)")
    assert p50 < 100.0, f"p50 retrieval {p50:.1f}ms exceeds 100ms"
