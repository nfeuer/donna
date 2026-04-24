"""End-to-end: vault file change → watcher → memory_search sees it.

Uses the deterministic :class:`FakeEmbeddingProvider` rather than
loading MiniLM so this stays a fast integration test. The real
MiniLM path is exercised by the `@pytest.mark.slow` variant in the
same directory.
"""
from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

import aiosqlite
import numpy as np
import pytest
import sqlite_vec

from donna.config import (
    MemoryConfig,
    VaultConfig,
    VaultRetrievalConfig,
    VaultSafetyConfig,
    VaultSourceConfig,
    VaultSourcesConfig,
)
from donna.integrations.vault import VaultClient
from donna.memory.chunking import MarkdownHeadingChunker
from donna.memory.queue import MemoryIngestQueue
from donna.memory.sources_vault import VaultSource
from donna.memory.store import MemoryStore


class FakeProvider:
    name = "fake"
    version_tag = "fake@v1"
    dim = 384
    max_tokens = 256

    async def embed(self, text: str) -> np.ndarray:
        return self._vec(text)

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None,
    ) -> list[np.ndarray]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


async def _open_db() -> tuple[aiosqlite.Connection, Path]:
    tmp = Path(tempfile.mkstemp(prefix="donna_vroundtrip_", suffix=".db")[1])
    tmp.unlink(missing_ok=True)
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp}")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    conn = await aiosqlite.connect(str(tmp))
    raw = conn._conn
    await conn._execute(raw.enable_load_extension, True)
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())
    return conn, tmp


def _build_memory_config(vault_root: Path) -> MemoryConfig:
    return MemoryConfig(
        vault=VaultConfig(root=str(vault_root)),
        safety=VaultSafetyConfig(),
        sources=VaultSourcesConfig(
            vault=VaultSourceConfig(
                enabled=True,
                chunker="markdown_heading",
                ignore_globs=["Templates/**", ".obsidian/**"],
            ),
        ),
    )


@pytest.mark.integration
async def test_backfill_ingests_and_search_returns_chunks(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "vault"
    (vault_root / "Inbox").mkdir(parents=True)
    note_path = vault_root / "Inbox" / "alpha.md"
    note_path.write_text(
        "# Alpha\n\nMemory store smoke test. alpha beta gamma paragraph.\n"
    )

    mem_cfg = _build_memory_config(vault_root)
    client = VaultClient(config=mem_cfg)

    conn, db_path = await _open_db()
    try:
        provider = FakeProvider()
        store = MemoryStore(
            conn, provider, MarkdownHeadingChunker(),
            VaultRetrievalConfig(default_k=5, max_k=10, min_score=0.0),
        )
        queue = MemoryIngestQueue(store, batch_size=4, flush_ms=50)
        source = VaultSource(
            client=client, store=store, queue=queue,
            cfg=mem_cfg.sources.vault, vault_cfg=mem_cfg.vault,
            user_id="nick",
        )

        worker = asyncio.create_task(queue.run_forever())
        try:
            n = await source.backfill("nick")
            # One .md under Inbox/ — Templates/.obsidian excluded by globs.
            assert n == 1
            # Flush the queue: sleep past flush_ms so the batch drains.
            for _ in range(10):
                await asyncio.sleep(0.1)
                if queue.qsize() == 0:
                    break
            # Give the worker a moment to commit.
            await asyncio.sleep(0.1)
            hits = await store.search(
                query="alpha beta gamma paragraph",
                user_id="nick",
                k=5,
            )
            assert hits
            assert any(h.source_path == "Inbox/alpha.md" for h in hits)
            assert any(h.heading_path == ["Alpha"] for h in hits)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
    finally:
        await conn.close()
        db_path.unlink(missing_ok=True)


@pytest.mark.integration
async def test_backfill_ignores_templates_and_obsidian(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    (vault_root / "Templates").mkdir(parents=True)
    (vault_root / ".obsidian").mkdir()
    (vault_root / "Inbox").mkdir()
    (vault_root / "Inbox" / "real.md").write_text("# Real\n\nIndex me.\n")
    (vault_root / "Templates" / "daily.md").write_text("# Ignore\n\nTemplate.\n")
    (vault_root / ".obsidian" / "workspace.md").write_text("# No\n\nIgnore.\n")

    mem_cfg = _build_memory_config(vault_root)
    client = VaultClient(config=mem_cfg)
    conn, db_path = await _open_db()
    try:
        provider = FakeProvider()
        store = MemoryStore(
            conn, provider, MarkdownHeadingChunker(),
            VaultRetrievalConfig(default_k=5, max_k=10, min_score=0.0),
        )
        queue = MemoryIngestQueue(store, batch_size=4, flush_ms=50)
        source = VaultSource(
            client=client, store=store, queue=queue,
            cfg=mem_cfg.sources.vault, vault_cfg=mem_cfg.vault,
            user_id="nick",
        )
        worker = asyncio.create_task(queue.run_forever())
        try:
            n = await source.backfill("nick")
            assert n == 1
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
    finally:
        await conn.close()
        db_path.unlink(missing_ok=True)


@pytest.mark.integration
async def test_backfill_sensitive_frontmatter_propagates(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    (vault_root / "Inbox").mkdir(parents=True)
    (vault_root / "Inbox" / "secret.md").write_text(
        "---\ndonna: local-only\n---\n\n# S\n\nsecret beta gamma\n"
    )

    mem_cfg = _build_memory_config(vault_root)
    client = VaultClient(config=mem_cfg)
    conn, db_path = await _open_db()
    try:
        provider = FakeProvider()
        store = MemoryStore(
            conn, provider, MarkdownHeadingChunker(),
            VaultRetrievalConfig(default_k=5, max_k=10, min_score=0.0),
        )
        queue = MemoryIngestQueue(store, batch_size=4, flush_ms=50)
        source = VaultSource(
            client=client, store=store, queue=queue,
            cfg=mem_cfg.sources.vault, vault_cfg=mem_cfg.vault,
            user_id="nick",
        )
        worker = asyncio.create_task(queue.run_forever())
        try:
            await source.backfill("nick")
            for _ in range(10):
                await asyncio.sleep(0.1)
                if queue.qsize() == 0:
                    break
            await asyncio.sleep(0.1)
            hits = await store.search(
                query="secret beta gamma", user_id="nick", k=5,
            )
            assert hits
            assert all(h.sensitive for h in hits)
            assert all(h.metadata.get("sensitive") is True for h in hits)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
    finally:
        await conn.close()
        db_path.unlink(missing_ok=True)
