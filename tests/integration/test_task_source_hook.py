"""Integration: task observer flows through ``Database.create_task`` / ``update_task``."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import sqlite_vec

from donna.config import TaskSourceConfig, VaultRetrievalConfig
from donna.memory.chunking import TaskChunker
from donna.memory.sources_task import SOURCE_TYPE as TASK_SOURCE_TYPE
from donna.memory.sources_task import TaskSource
from donna.memory.store import MemoryStore
from donna.tasks.database import Database
from donna.tasks.db_models import TaskStatus
from donna.tasks.state_machine import StateMachine


class _FakeProvider:
    name = "fake"
    version_tag = "fake@v1"
    dim = 384
    max_tokens = 256

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, text: str) -> np.ndarray:
        return self._vec(text)

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None,
    ) -> list[np.ndarray]:
        self.calls.append(list(texts))
        self.last_task_type = task_type
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
) -> AsyncIterator[tuple[Database, MemoryStore, _FakeProvider, TaskSource]]:
    db_path = tmp_path / "wired.db"
    db = Database(db_path=str(db_path), state_machine=state_machine)
    await db.connect()
    await asyncio.to_thread(_alembic_upgrade, db_path)
    conn = db.connection
    raw = conn._conn  # type: ignore[attr-defined]
    await conn._execute(raw.enable_load_extension, True)  # type: ignore[attr-defined]
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())  # type: ignore[attr-defined]

    provider = _FakeProvider()
    chunker = TaskChunker(max_tokens=256)
    store = MemoryStore(
        conn, provider, chunker, VaultRetrievalConfig(min_score=0.0),
    )
    source = TaskSource(store=store, cfg=TaskSourceConfig(enabled=True))

    class _Combined:
        async def observe_task(self, event: dict) -> None:
            await source.observe_task(event)

    db.set_memory_observer(_Combined())
    try:
        yield db, store, provider, source
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_task_triggers_upsert(
    wired_db: tuple[Database, MemoryStore, _FakeProvider, TaskSource],
) -> None:
    db, store, _provider, _source = wired_db
    task = await db.create_task(
        user_id="nick", title="Draft onboarding deck for Sarah",
    )
    # Observer is fire-and-forget — yield the loop a couple times.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    hits = await store.search(query="onboarding deck", user_id="nick", sources=["task"])
    assert hits
    assert hits[0].source_type == TASK_SOURCE_TYPE
    assert hits[0].source_id == task.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_unchanged_fields_does_not_reembed(
    wired_db: tuple[Database, MemoryStore, _FakeProvider, TaskSource],
) -> None:
    db, _store, provider, _source = wired_db
    task = await db.create_task(user_id="nick", title="Alpha")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    calls_before = len(provider.calls)

    # Update a non-semantic field (priority) — no new embed batch.
    await db.update_task(task.id, priority=4)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(provider.calls) == calls_before


@pytest.mark.asyncio
@pytest.mark.integration
async def test_status_done_forces_reembed(
    wired_db: tuple[Database, MemoryStore, _FakeProvider, TaskSource],
) -> None:
    db, _store, provider, _source = wired_db
    task = await db.create_task(user_id="nick", title="Beta")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    calls_before = len(provider.calls)

    await db.update_task(task.id, status=TaskStatus.DONE)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(provider.calls) > calls_before
