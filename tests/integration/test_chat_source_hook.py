"""Integration: chat observer flows through ``Database.add_chat_message``.

Uses a real :class:`Database` (alembic-migrated tmp file) + a
FakeEmbeddingProvider-backed :class:`MemoryStore` + a
:class:`ChatSource`, then drives the DB methods directly and asserts
that ``memory_search(sources=["chat"])`` surfaces the flushed turn.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
import sqlite_vec

from donna.config import ChatSourceConfig, VaultRetrievalConfig
from donna.memory.chunking import ChatTurnChunker
from donna.memory.sources_chat import SOURCE_TYPE as CHAT_SOURCE_TYPE
from donna.memory.sources_chat import ChatSource
from donna.memory.store import MemoryStore
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


@pytest_asyncio.fixture
async def wired_db(
    tmp_path: Path, state_machine: StateMachine,
) -> AsyncIterator[tuple[Database, MemoryStore, ChatSource]]:
    db_path = tmp_path / "wired.db"
    db = Database(db_path=str(db_path), state_machine=state_machine)
    await db.connect()
    # Alembic builds the full schema including memory_* tables.
    await asyncio.to_thread(_alembic_upgrade, db_path)
    # Load sqlite-vec onto the live aiosqlite connection.
    conn = db.connection
    raw = conn._conn  # type: ignore[attr-defined]
    await conn._execute(raw.enable_load_extension, True)  # type: ignore[attr-defined]
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())  # type: ignore[attr-defined]

    provider = _FakeProvider()
    chunker = ChatTurnChunker(max_tokens=64, min_chars=1)
    store = MemoryStore(
        conn, provider, chunker, VaultRetrievalConfig(min_score=0.0),
    )
    source = ChatSource(
        store=store, cfg=ChatSourceConfig(enabled=True, min_chars=1),
    )

    class _Combined:
        async def observe_message(self, event: dict) -> None:
            await source.observe_message(event)

        async def observe_session_closed(self, event: dict) -> None:
            await source.observe_session_closed(event)

    db.set_memory_observer(_Combined())
    try:
        yield db, store, source
    finally:
        await db.close()


def _alembic_upgrade(db_path: Path) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_add_chat_message_flushes_on_role_flip(
    wired_db: tuple[Database, MemoryStore, ChatSource],
) -> None:
    db, store, _source = wired_db
    session = await db.create_chat_session(user_id="nick", channel="discord")

    await db.add_chat_message(
        session.id, role="user", content="Plan the week, anything urgent?",
    )
    await db.add_chat_message(
        session.id, role="user", content="Also: schedule a 1:1 with Sarah.",
    )
    # Role flip — should flush the user turn.
    await db.add_chat_message(
        session.id, role="assistant", content="Urgent items pulled; draft ready.",
    )
    # Give the scheduled observer tasks a chance to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    hits = await store.search(query="schedule a 1:1", user_id="nick", sources=["chat"])
    assert hits, "expected at least one chat hit after role flip"
    assert all(h.source_type == CHAT_SOURCE_TYPE for h in hits)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_close_flushes_pending_turn(
    wired_db: tuple[Database, MemoryStore, ChatSource],
) -> None:
    db, store, _source = wired_db
    session = await db.create_chat_session(user_id="nick", channel="discord")
    await db.add_chat_message(
        session.id, role="user", content="Remind me to call mom tomorrow.",
    )
    # Let the message observer run before we close.
    await asyncio.sleep(0)
    await db.update_chat_session(session.id, status="closed")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    hits = await store.search(query="call mom", user_id="nick", sources=["chat"])
    assert hits
