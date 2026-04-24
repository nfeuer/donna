"""Integration: correction logger dispatches via the module-level registry."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import sqlite_vec

from donna.config import CorrectionSourceConfig, VaultRetrievalConfig
from donna.memory.chunking import MarkdownHeadingChunker
from donna.memory.observers import register_observer, unregister_all
from donna.memory.sources_correction import SOURCE_TYPE as CORR_SOURCE_TYPE
from donna.memory.sources_correction import CorrectionSource
from donna.memory.store import MemoryStore
from donna.preferences.correction_logger import log_correction
from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


class _FakeProvider:
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
        n = np.linalg.norm(v)
        return v / n if n > 0 else v


def _alembic_upgrade(db_path: Path) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def wired_db(
    tmp_path: Path, state_machine: StateMachine,
) -> AsyncIterator[tuple[Database, MemoryStore, CorrectionSource]]:
    db_path = tmp_path / "wired.db"
    db = Database(db_path=str(db_path), state_machine=state_machine)
    await db.connect()
    await asyncio.to_thread(_alembic_upgrade, db_path)
    conn = db.connection
    raw = conn._conn  # type: ignore[attr-defined]
    await conn._execute(raw.enable_load_extension, True)  # type: ignore[attr-defined]
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())  # type: ignore[attr-defined]

    provider = _FakeProvider()
    chunker = MarkdownHeadingChunker(max_tokens=128, overlap_tokens=8, min_tokens=4)
    store = MemoryStore(
        conn, provider, chunker, VaultRetrievalConfig(min_score=0.0),
    )
    corr = CorrectionSource(
        store=store, cfg=CorrectionSourceConfig(enabled=True),
    )
    unregister_all("correction")
    register_observer("correction", corr.observe)
    try:
        yield db, store, corr
    finally:
        unregister_all("correction")
        await db.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_log_correction_populates_memory(
    wired_db: tuple[Database, MemoryStore, CorrectionSource],
) -> None:
    db, store, _source = wired_db
    await log_correction(
        db,
        user_id="nick",
        task_id="t-123",
        task_type="classify_priority",
        field="priority",
        original="2",
        corrected="4",
        input_text="urgent email from board",
    )
    # Observer dispatch is fire-and-forget — yield the loop.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    hits = await store.search(
        query="urgent email", user_id="nick", sources=[CORR_SOURCE_TYPE],
    )
    assert hits
    assert hits[0].source_type == CORR_SOURCE_TYPE
