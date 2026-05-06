"""Unit tests for slice 20 ChatPromptBuilder.

Realizes the verification leg of
``docs/superpowers/specs/manual-escalation.md`` §5.2 (prompt rendering
+ workspace write) and §10.2 row 3 (deterministic Ollama fallback).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import PromptDeliveryConfig
from donna.cost.escalation_chat_prompt import ChatPromptBuilder
from donna.cost.escalation_repository import EscalationRequestRow

_REPO_ROOT = Path(__file__).resolve().parents[2]


_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT,
    parent_escalation_id INTEGER REFERENCES escalation_request(id)
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "chat_prompt.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


def _row(**overrides) -> EscalationRequestRow:
    base = {
        "id": 1,
        "user_id": "nick",
        "correlation_id": "test-corr-1",
        "task_id": "task-1",
        "task_type": "chat_escalation",
        "estimate_usd": 7.5,
        "daily_remaining_usd": 2.5,
        "offered_modes": ["chat", "pause", "cancel"],
        "resolution": None,
        "resolved_by": None,
        "resolved_at": None,
        "iteration": 1,
        "status": "open",
        "created_at": datetime.now(tz=UTC),
        "priority": 2,
        "delivery_status": "pending",
        "delivery_attempts": 0,
        "last_delivery_attempt_at": None,
    }
    base.update(overrides)
    return EscalationRequestRow(**base)


async def _insert_minimal_row(conn: aiosqlite.Connection, row: EscalationRequestRow) -> int:
    cur = await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_id, task_type,
            estimate_usd, daily_remaining_usd, offered_modes,
            iteration, status, created_at, priority,
            delivery_status, delivery_attempts
        )
        VALUES (?, ?, ?, ?, ?, ?, '["chat","pause","cancel"]', 1, 'open', ?, 2, 'pending', 0)
        """,
        (
            row.user_id,
            row.correlation_id,
            row.task_id,
            row.task_type,
            row.estimate_usd,
            row.daily_remaining_usd,
            row.created_at.isoformat(),
        ),
    )
    await conn.commit()
    new_id = cur.lastrowid
    assert new_id is not None
    return int(new_id)


def _builder_with_router_mock(
    *, tmp_path: Path, complete: AsyncMock
) -> tuple[ChatPromptBuilder, MagicMock]:
    """Return a builder whose router returns whatever the AsyncMock yields."""
    router = MagicMock()
    router.complete = complete
    router.get_prompt_template = MagicMock(
        return_value="SUMMARY-TEMPLATE: {{ original_prompt }}"
    )
    cfg = PromptDeliveryConfig()
    builder = ChatPromptBuilder(
        router=router,
        project_root=_REPO_ROOT,
        config=cfg,
        workspace_root=tmp_path / "workspace",
    )
    return builder, router


# ---------------------------------------------------------------------
# Rendering + persistence
# ---------------------------------------------------------------------


class TestRendering:
    async def test_renders_template_and_persists_columns(
        self, conn: aiosqlite.Connection, tmp_path: Path
    ) -> None:
        complete = AsyncMock(return_value=({"title": "T", "summary": "S"}, MagicMock()))
        builder, _ = _builder_with_router_mock(tmp_path=tmp_path, complete=complete)
        row = _row()
        new_id = await _insert_minimal_row(conn, row)
        row = _row(id=new_id)

        prompt_body, summary, prompt_path = await builder.build_and_persist(
            conn=conn,
            row=row,
            original_prompt="Sample original question?",
        )

        # Template tokens are rendered.
        assert "Sample original question?" in prompt_body
        assert "test-corr-1" in prompt_body
        assert "chat_escalation" in prompt_body
        # Workspace write happened.
        assert prompt_path is not None
        assert Path(prompt_path).read_text(encoding="utf-8") == prompt_body
        # Summary is the stitched router output.
        assert summary == "T — S"

        cur = await conn.execute(
            "SELECT prompt_body, summary, prompt_path, mode "
            "FROM escalation_request WHERE id = ?",
            (new_id,),
        )
        record = await cur.fetchone()
        assert record is not None
        assert record[0] == prompt_body
        assert record[1] == "T — S"
        assert record[2] == prompt_path
        assert record[3] == "chat"

    async def test_summary_truncated_to_max_chars(
        self, conn: aiosqlite.Connection, tmp_path: Path
    ) -> None:
        # Force a long-but-schema-valid summary so the truncate path
        # runs without tripping ``schemas/escalation_summary_output.json``'s
        # maxLength=800. Configure ``discord_summary_max_chars`` low so the
        # truncation kicks in well below schema bounds.
        long_summary = "A" * 600
        complete = AsyncMock(
            return_value=({"title": "Big", "summary": long_summary}, MagicMock())
        )
        builder, _ = _builder_with_router_mock(tmp_path=tmp_path, complete=complete)
        # Override the discord cap to force truncation.
        builder._config = PromptDeliveryConfig(discord_summary_max_chars=120)
        row = _row()
        new_id = await _insert_minimal_row(conn, row)
        row = _row(id=new_id)

        _body, summary, _path = await builder.build_and_persist(
            conn=conn,
            row=row,
            original_prompt="Question",
        )
        assert len(summary) <= 120
        assert summary.endswith("…")

    async def test_falls_back_to_deterministic_summary_on_router_error(
        self, conn: aiosqlite.Connection, tmp_path: Path
    ) -> None:
        complete = AsyncMock(side_effect=RuntimeError("ollama down"))
        builder, _ = _builder_with_router_mock(tmp_path=tmp_path, complete=complete)
        row = _row()
        new_id = await _insert_minimal_row(conn, row)
        row = _row(id=new_id, estimate_usd=12.34)

        _body, summary, _path = await builder.build_and_persist(
            conn=conn,
            row=row,
            original_prompt="What is the capital of France?",
        )
        assert "$12.34" in summary
        assert "Click for full prompt." in summary
        assert "chat_escalation" in summary

    async def test_falls_back_when_router_returns_empty_summary(
        self, conn: aiosqlite.Connection, tmp_path: Path
    ) -> None:
        complete = AsyncMock(return_value=({"title": "", "summary": ""}, MagicMock()))
        builder, _ = _builder_with_router_mock(tmp_path=tmp_path, complete=complete)
        row = _row()
        new_id = await _insert_minimal_row(conn, row)
        row = _row(id=new_id)

        _body, summary, _path = await builder.build_and_persist(
            conn=conn,
            row=row,
            original_prompt="Q?",
        )
        # Falls back to the deterministic templated summary.
        assert "Click for full prompt." in summary

    async def test_falls_back_on_schema_violation(
        self, conn: aiosqlite.Connection, tmp_path: Path
    ) -> None:
        """Required ``title`` missing → ``jsonschema.validate`` raises →
        deterministic fallback (§10.2 row 3)."""
        complete = AsyncMock(
            return_value=({"summary": "no title here"}, MagicMock())
        )
        builder, _ = _builder_with_router_mock(tmp_path=tmp_path, complete=complete)
        row = _row()
        new_id = await _insert_minimal_row(conn, row)
        row = _row(id=new_id)

        _body, summary, _path = await builder.build_and_persist(
            conn=conn,
            row=row,
            original_prompt="Q?",
        )
        assert "Click for full prompt." in summary

    async def test_workspace_write_failure_returns_none_path(
        self, conn: aiosqlite.Connection, tmp_path: Path, monkeypatch
    ) -> None:
        complete = AsyncMock(return_value=({"title": "T", "summary": "S"}, MagicMock()))
        builder, _ = _builder_with_router_mock(tmp_path=tmp_path, complete=complete)

        # Force the synchronous file write to raise.
        from donna.cost import escalation_chat_prompt as _module

        def _explode(*_args, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(_module, "_write_file_sync", _explode)

        row = _row()
        new_id = await _insert_minimal_row(conn, row)
        row = _row(id=new_id)

        _body, _summary, prompt_path = await builder.build_and_persist(
            conn=conn,
            row=row,
            original_prompt="Q?",
        )
        assert prompt_path is None
        cur = await conn.execute(
            "SELECT prompt_path FROM escalation_request WHERE id = ?", (new_id,)
        )
        record = await cur.fetchone()
        assert record is not None
        assert record[0] is None
