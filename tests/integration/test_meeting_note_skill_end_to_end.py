"""Slice 15 — end-to-end meeting-note autowrite.

Exercises every seam: VaultTemplateRenderer, MemoryInformedWriter,
MeetingNoteSkill, VaultWriter (real git commit), VaultClient,
MemoryStore (post-filtered prior-meeting hit), and person-link
resolution (existing + missing attendee).

The LLM is replaced with a deterministic ``_FakeRouter`` that returns
the same JSON blob every time; the point is to lock down the skill
composition, not the prompt quality.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import sqlite_vec
import structlog
import structlog.testing

from donna.capabilities.meeting_note_skill import (
    CalendarEventRow,
    MeetingNoteSkill,
)
from donna.config import (
    MeetingNoteSkillConfig,
    MemoryConfig,
    VaultConfig,
    VaultRetrievalConfig,
    VaultSafetyConfig,
)
from donna.integrations.git_repo import GitRepo
from donna.integrations.vault import VaultClient, VaultWriter
from donna.memory.chunking import MarkdownHeadingChunker
from donna.memory.store import Document, MemoryStore
from donna.memory.templates import VaultTemplateRenderer
from donna.memory.writer import MemoryInformedWriter
from donna.models.types import CompletionMetadata


class _FakeEmbeddingProvider:
    """Deterministic hash-based embedding provider for the E2E test."""

    name = "fake"
    version_tag = "fake@v1"
    dim = 384
    max_tokens = 256

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> np.ndarray:
        self.calls.append(text)
        return self._vec(text)

    async def embed_batch(
        self, texts: list[str], *, task_type: str | None = None
    ) -> list[np.ndarray]:
        self.calls.extend(texts)
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) & 0xFFFFFFFF
        vec = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        n = np.linalg.norm(vec)
        return vec / n if n else vec


class _FakeRouter:
    """Stands in for :class:`ModelRouter`. No retries, no budget."""

    FIXED_PROMPT = "draft meeting note: {{ event.summary }}"

    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []

    def get_prompt_template(self, task_type: str) -> str:
        return self.FIXED_PROMPT

    async def complete(
        self,
        prompt: str,
        task_type: str,
        task_id: str | None = None,
        user_id: str = "system",
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        self.complete_calls.append(
            {"prompt": prompt, "task_type": task_type, "user_id": user_id}
        )
        return (
            {
                "summary": "You discussed onboarding and priorities. Did you confirm owners?",
                "action_item_candidates": [
                    "Send Alice the onboarding deck",
                    "Schedule a follow-up with Bob",
                ],
                "open_questions": ["Who owns the Phase 2 rollout?"],
                "links_suggested": ["[[Projects/Onboarding]]"],
            },
            CompletionMetadata(
                latency_ms=150,
                tokens_in=200,
                tokens_out=120,
                cost_usd=0.003,
                model_actual="anthropic/claude-sonnet-4-20250514",
            ),
        )


async def _open_db() -> tuple[aiosqlite.Connection, Path]:
    tmp = Path(tempfile.mkstemp(prefix="donna_meet_e2e_", suffix=".db")[1])
    tmp.unlink(missing_ok=True)
    from alembic.config import Config as AlembicConfig

    from alembic import command

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp}")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    conn = await aiosqlite.connect(str(tmp))
    await conn.execute("PRAGMA foreign_keys=ON")
    raw = conn._conn
    await conn._execute(raw.enable_load_extension, True)
    await conn._execute(raw.load_extension, sqlite_vec.loadable_path())
    return conn, tmp


@pytest_asyncio.fixture
async def db_conn() -> AsyncIterator[aiosqlite.Connection]:
    conn, path = await _open_db()
    try:
        yield conn
    finally:
        await conn.close()
        path.unlink(missing_ok=True)


def _vault_cfg(root: Path) -> MemoryConfig:
    return MemoryConfig(
        vault=VaultConfig(
            root=str(root),
            git_author_name="Donna IT",
            git_author_email="donna-it@example.com",
            sync_method="manual",
        ),
        safety=VaultSafetyConfig(
            max_note_bytes=50_000,
            path_allowlist=["Inbox", "Meetings", "People"],
        ),
    )


@pytest.mark.asyncio
async def test_meeting_note_skill_end_to_end(
    db_conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    # --- Set up a real git vault --------------------------------------
    cfg = _vault_cfg(tmp_path)
    git = GitRepo(
        root=Path(cfg.vault.root),
        author_name=cfg.vault.git_author_name,
        author_email=cfg.vault.git_author_email,
    )
    vault_client = VaultClient(cfg)
    vault_writer = VaultWriter(cfg, git, client=vault_client)
    await vault_writer.ensure_ready()

    # Seed People/Alice.md (exists) but NOT Bob.md (unresolved).
    await vault_writer.write("People/Alice.md", "# Alice\n\nNotes about Alice.\n")

    # --- Seed memory store with a prior meeting + a chat --------------
    provider = _FakeEmbeddingProvider()
    chunker = MarkdownHeadingChunker(max_tokens=60, overlap_tokens=8, min_tokens=5)
    retrieval = VaultRetrievalConfig(default_k=10, min_score=0.0, max_k=50)
    memory_store = MemoryStore(db_conn, provider, chunker, retrieval)

    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Meetings/2026-04-17-prev.md",
            title="Prev Sync",
            uri="vault:Meetings/2026-04-17-prev.md",
            content="# Previous Sync\n\nDiscussed onboarding deck with Alice.\n",
            metadata={"type": "meeting", "calendar_event_id": "E_prev"},
        )
    )
    await memory_store.upsert(
        Document(
            user_id="nick",
            source_type="chat",
            source_id="session-1:msg-1-msg-2",
            title="Chat about Alice",
            uri=None,
            content="user: Heads up, Alice needs the Phase 2 rollout plan.\n",
        )
    )

    # --- Build the skill and run --------------------------------------
    templates_dir = Path(__file__).resolve().parents[2] / "prompts" / "vault"
    renderer = VaultTemplateRenderer(templates_dir)
    router = _FakeRouter()
    writer = MemoryInformedWriter(
        renderer=renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=router,  # type: ignore[arg-type]
        logger=object(),  # type: ignore[arg-type]
    )
    skill = MeetingNoteSkill(
        writer=writer,
        memory_store=memory_store,
        vault_client=vault_client,
        config=MeetingNoteSkillConfig(autonomy_level="medium"),
        user_id="nick",
    )

    now = datetime.utcnow()
    event = CalendarEventRow(
        event_id="E_new",
        user_id="nick",
        calendar_id="primary",
        summary="Onboarding Sync",
        start_time=now - timedelta(minutes=32),
        end_time=now - timedelta(minutes=2),
        attendees=json.dumps(
            [
                {"name": "Alice", "email": "alice@example.com"},
                {"name": "Bob", "email": "bob@example.com"},
            ]
        ),
    )

    with structlog.testing.capture_logs() as first_events:
        result = await skill.run_for_event(event)

    assert result.skipped is False
    assert result.sha is not None and len(result.sha) == 40

    # --- File-on-disk assertions --------------------------------------
    written_path = Path(cfg.vault.root) / "Meetings"
    md_files = sorted(written_path.glob("*.md"))
    assert len(md_files) == 1
    note_text = md_files[0].read_text(encoding="utf-8")

    # Frontmatter (python-frontmatter round-trip preserves keys).
    import frontmatter as fm_lib

    post = fm_lib.loads(note_text)
    assert post["calendar_event_id"] == "E_new"
    assert post["idempotency_key"] == "E_new"
    assert post["autowritten_by"] == "donna"
    assert post["type"] == "meeting"

    # Body: Alice resolves to namespaced, Bob stays bare.
    assert "[[People/Alice]]" in post.content
    assert "[[Bob]]" in post.content
    # Prior meeting referenced (the stored path with .md stripped).
    assert "[[Meetings/2026-04-17-prev]]" in post.content

    # --- Git commit assertion -----------------------------------------
    log = subprocess.run(
        ["git", "-C", cfg.vault.root, "log", "--oneline"],
        check=True,
        capture_output=True,
        text=True,
    )
    autowrite_commits = [
        line for line in log.stdout.splitlines() if "autowrite:" in line
    ]
    assert len(autowrite_commits) == 1
    assert "E_new" in autowrite_commits[0]

    # Structlog happy-path event.
    written_events = [e for e in first_events if e["event"] == "meeting_note_written"]
    assert len(written_events) == 1

    # --- Idempotent re-run: no new commit -----------------------------
    with structlog.testing.capture_logs() as second_events:
        second = await skill.run_for_event(event)

    assert second.skipped is True
    assert second.reason == "idempotent"

    log2 = subprocess.run(
        ["git", "-C", cfg.vault.root, "log", "--oneline"],
        check=True,
        capture_output=True,
        text=True,
    )
    autowrite_commits_2 = [
        line for line in log2.stdout.splitlines() if "autowrite:" in line
    ]
    assert len(autowrite_commits_2) == 1  # unchanged

    skip_events = [
        e for e in second_events if e["event"] == "meeting_note_skipped_idempotent"
    ]
    assert len(skip_events) == 1
    # Router not called the second time.
    assert len(router.complete_calls) == 1
