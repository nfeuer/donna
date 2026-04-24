"""End-to-end: semantic memory spans all four source types.

Seeds vault notes, chat messages, tasks, and corrections into one
memory index, then asserts:

- ``memory_search`` with no ``sources`` filter returns hits from
  multiple source types;
- ``memory_search(sources=["chat"])`` / ``["task"]`` / ``["vault"]``
  / ``["correction"]`` each only return hits of that type.

Uses the :class:`FakeEmbeddingProvider` so the seed path stays fast.
The slice-13 vault roundtrip test already exercises the real MiniLM
path behind ``@pytest.mark.slow``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import sqlite_vec

from donna.config import (
    ChatSourceConfig,
    CorrectionSourceConfig,
    TaskSourceConfig,
    VaultRetrievalConfig,
)
from donna.memory.chunking import ChatTurnChunker, MarkdownHeadingChunker
from donna.memory.sources_chat import ChatSource
from donna.memory.sources_correction import CorrectionSource
from donna.memory.sources_task import TaskSource
from donna.memory.store import Document, MemoryStore


class _SeedProvider:
    """Deterministic provider that gives vault and chat docs the same
    direction when they share keywords.

    The real MiniLM handles semantic similarity for us; here we cheat
    with an overlapping hash that keys off the presence of a few
    anchor words in both the query and the stored text, so the test
    can distinguish a genuine source-filter hit from a noisy one.
    """

    name = "seed-fake"
    version_tag = "seed-fake@v1"
    dim = 384
    max_tokens = 256

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> np.ndarray:
        self.calls.append(text)
        return self._vec(text)

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None,
    ) -> list[np.ndarray]:
        self.calls.extend(texts)
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> np.ndarray:
        text_l = text.lower()
        # Build a deterministic base vector per text and nudge towards
        # topic vectors when anchor words are present.
        seed = abs(hash(text)) & 0xFFFFFFFF
        base = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        base *= 0.1  # small base noise
        topics = {
            "onboarding": 0,
            "sarah": 1,
            "priority": 2,
            "call": 3,
            "mom": 4,
            "roadmap": 5,
            "vault": 6,
        }
        for token, idx in topics.items():
            if token in text_l:
                base[idx % self.dim] += 2.0
        norm = np.linalg.norm(base)
        return base / norm if norm > 0 else base


@pytest_asyncio.fixture
async def seeded_store(
    tmp_path: Path,
) -> AsyncIterator[tuple[MemoryStore, _SeedProvider]]:
    db_path = tmp_path / "e2e.db"
    db_path.unlink(missing_ok=True)

    # Apply alembic migrations via a worker thread, then open an
    # async connection with vec0 loaded.
    def _upgrade() -> None:
        from alembic.config import Config

        from alembic import command

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)

    conn = await aiosqlite.connect(str(db_path))
    raw = conn._conn  # type: ignore[attr-defined]
    await conn._execute(raw.enable_load_extension, True)  # type: ignore[attr-defined]
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())  # type: ignore[attr-defined]

    provider = _SeedProvider()
    chunker = MarkdownHeadingChunker(max_tokens=128, overlap_tokens=8, min_tokens=4)
    store = MemoryStore(
        conn, provider, chunker, VaultRetrievalConfig(min_score=0.0, max_k=32),
    )

    # Seed vault directly as Documents (no VaultSource needed).
    for i in range(3):
        await store.upsert(
            Document(
                user_id="nick",
                source_type="vault",
                source_id=f"Meetings/2026-04-0{i+1}-standup.md",
                title=f"Standup {i+1}",
                uri=f"vault:Meetings/2026-04-0{i+1}-standup.md",
                content=(
                    "# Standup\n\nDiscussed Sarah onboarding roadmap.\n\n"
                    "Decided to prioritise the onboarding deck."
                ),
            )
        )

    # Seed chat turns (use ChatSource backfill after seeding raw rows).
    await conn.execute(
        "INSERT INTO conversation_sessions "
        "(id, user_id, channel, status, created_at, last_activity, "
        " expires_at, message_count) "
        "VALUES ('sess-e2e','nick','discord','active','2026-04-01',"
        "        '2026-04-01','2026-04-02', 3)"
    )
    for i, (role, content) in enumerate(
        [
            ("user", "What did we decide about Sarah's onboarding?"),
            ("assistant", "Roadmap: onboarding deck ships Friday."),
            ("user", "Call mom after the standup."),
        ],
        start=1,
    ):
        await conn.execute(
            "INSERT INTO conversation_messages "
            "(id, session_id, role, content, created_at) VALUES "
            "(?, 'sess-e2e', ?, ?, ?)",
            (f"e{i}", role, content, f"2026-04-01T10:0{i}"),
        )
    await conn.commit()
    chat = ChatSource(
        store=store,
        cfg=ChatSourceConfig(enabled=True, min_chars=1),
    )
    # Re-attach the chat chunker so retrieval doesn't fight with the
    # markdown chunker used above for vault docs.
    chat._chunker = ChatTurnChunker(max_tokens=256, min_chars=1)  # type: ignore[attr-defined]
    await chat.backfill("nick")

    # Seed tasks via raw INSERT + TaskSource backfill.
    for tid, title, notes in [
        ("task-1", "Draft onboarding deck for Sarah", '["outline","review"]'),
        ("task-2", "Call mom after standup", '["reminder"]'),
    ]:
        await conn.execute(
            "INSERT INTO tasks (id, user_id, title, description, domain, "
            "priority, status, estimated_duration, deadline, deadline_type, "
            "scheduled_start, actual_start, completed_at, recurrence, "
            "dependencies, parent_task, prep_work_flag, prep_work_instructions, "
            "agent_eligible, assigned_agent, agent_status, tags, notes, "
            "reschedule_count, created_at, created_via, estimated_cost, "
            "calendar_event_id, donna_managed, nudge_count, quality_score, "
            "capability_name, inputs_json) VALUES "
            "(?, 'nick', ?, NULL, 'personal', 3, 'backlog', NULL, NULL, 'none', "
            " NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL, 0, NULL, NULL, "
            " NULL, ?, 0, '2026-04-01', 'discord', NULL, NULL, 0, 0, NULL, "
            " NULL, NULL)",
            (tid, title, notes),
        )
    await conn.commit()
    task = TaskSource(store=store, cfg=TaskSourceConfig(enabled=True))
    await task.backfill("nick")

    # Seed corrections + backfill.
    await conn.execute(
        "INSERT INTO correction_log "
        "(id, timestamp, user_id, task_type, task_id, input_text, "
        " field_corrected, original_value, corrected_value) VALUES "
        "('corr-1','2026-04-01','nick','classify_priority','task-1',"
        " 'urgent email','priority','2','4')"
    )
    await conn.commit()
    corr = CorrectionSource(store=store, cfg=CorrectionSourceConfig(enabled=True))
    await corr.backfill("nick")

    try:
        yield store, provider
    finally:
        await conn.close()
        db_path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_memory_search_spans_all_sources(
    seeded_store: tuple[MemoryStore, _SeedProvider],
) -> None:
    store, _provider = seeded_store
    hits = await store.search(
        query="Sarah onboarding roadmap", user_id="nick", k=32,
    )
    assert hits, "expected hits across sources"
    seen = {h.source_type for h in hits}
    assert "vault" in seen
    assert "chat" in seen or "task" in seen


@pytest.mark.asyncio
@pytest.mark.integration
async def test_source_filters_are_scoped(
    seeded_store: tuple[MemoryStore, _SeedProvider],
) -> None:
    store, _provider = seeded_store
    chat_hits = await store.search(
        query="Sarah onboarding", user_id="nick", sources=["chat"], k=16,
    )
    task_hits = await store.search(
        query="onboarding deck", user_id="nick", sources=["task"], k=16,
    )
    corr_hits = await store.search(
        query="priority", user_id="nick", sources=["correction"], k=16,
    )
    vault_hits = await store.search(
        query="Sarah onboarding", user_id="nick", sources=["vault"], k=16,
    )
    assert chat_hits and all(h.source_type == "chat" for h in chat_hits)
    assert task_hits and all(h.source_type == "task" for h in task_hits)
    assert corr_hits and all(h.source_type == "correction" for h in corr_hits)
    assert vault_hits and all(h.source_type == "vault" for h in vault_hits)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multi_source_filter_mixes_two_types(
    seeded_store: tuple[MemoryStore, _SeedProvider],
) -> None:
    store, _provider = seeded_store
    mixed = await store.search(
        query="onboarding Sarah",
        user_id="nick",
        sources=["chat", "task"],
        k=32,
    )
    types = {h.source_type for h in mixed}
    assert types.issubset({"chat", "task"})
    assert types == {"chat", "task"}, f"expected both chat+task, got {types}"
