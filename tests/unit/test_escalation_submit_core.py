"""Unit tests for the channel-agnostic submit-flow (slice 21).

The core function is the shared backbone for the HTTP route and the
``/donna submit`` slash command — these tests cover the typed
exceptions and the row state transitions independently of either
surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from donna.cost.escalation_audit import ESCALATION_TASK_TYPE
from donna.cost.escalation_submit import (
    ConcurrentSubmissionError,
    IterationCapReachedError,
    ModeMismatchError,
    NotAwaitingSubmissionError,
    NotFoundError,
    SchemaValidationError,
    submit_escalation_core,
)

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
    parent_escalation_id INTEGER,
    human_review INTEGER NOT NULL DEFAULT 0,
    target_paths TEXT,
    originating_entity_type TEXT,
    originating_entity_id TEXT,
    base_sha TEXT,
    merged_at TEXT
);
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "submit.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


async def _seed(
    conn: aiosqlite.Connection,
    *,
    correlation_id: str = "cc-1",
    status: str = "resolved",
    mode: str | None = "claude_code",
    iteration: int = 1,
) -> None:
    await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_type, estimate_usd, daily_remaining_usd,
            offered_modes, mode, status, iteration, created_at, priority
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "nick", correlation_id, "skill_auto_draft", 6.0, 1.0,
            json.dumps(["claude_code", "pause", "cancel"]),
            mode, status, iteration,
            "2026-05-06T12:00:00+00:00", 2,
        ),
    )
    await conn.commit()


async def test_clean_first_submit_marks_submitted(conn: aiosqlite.Connection) -> None:
    await _seed(conn)
    result = await submit_escalation_core(
        conn, "cc-1", {"mode": "claude_code", "branch": "escalation/abc-foo"}
    )
    assert result.status == "submitted"
    assert result.iteration == 1


async def test_resubmit_after_failure_increments_iteration(
    conn: aiosqlite.Connection,
) -> None:
    await _seed(conn, status="failed", iteration=1)
    result = await submit_escalation_core(
        conn, "cc-1", {"mode": "claude_code", "branch": "escalation/abc-foo"}
    )
    assert result.iteration == 2
    assert result.status == "submitted"


async def test_iteration_cap_raises(conn: aiosqlite.Connection) -> None:
    await _seed(conn, status="failed", iteration=3)
    with pytest.raises(IterationCapReachedError) as exc:
        await submit_escalation_core(
            conn, "cc-1", {"mode": "claude_code", "branch": "escalation/abc-foo"}
        )
    assert exc.value.iteration == 3
    assert exc.value.limit == 3


async def test_not_found_raises(conn: aiosqlite.Connection) -> None:
    with pytest.raises(NotFoundError):
        await submit_escalation_core(
            conn, "missing", {"mode": "claude_code", "branch": "x"}
        )


async def test_open_status_rejected(conn: aiosqlite.Connection) -> None:
    await _seed(conn, status="open", mode=None)
    with pytest.raises(NotAwaitingSubmissionError) as exc:
        await submit_escalation_core(
            conn, "cc-1", {"mode": "claude_code", "branch": "x"}
        )
    assert exc.value.status == "open"


async def test_mode_mismatch_raises(conn: aiosqlite.Connection) -> None:
    await _seed(conn, mode="chat")
    with pytest.raises(ModeMismatchError):
        await submit_escalation_core(
            conn, "cc-1", {"mode": "claude_code", "branch": "x"}
        )


async def test_schema_validation_rejects_short_chat_answer(
    conn: aiosqlite.Connection,
) -> None:
    await _seed(conn, mode="chat")
    with pytest.raises(SchemaValidationError):
        await submit_escalation_core(
            conn, "cc-1", {"mode": "chat", "answer": "too short"}
        )


async def test_audit_event_written(conn: aiosqlite.Connection) -> None:
    await _seed(conn)
    await submit_escalation_core(
        conn, "cc-1", {"mode": "claude_code", "branch": "escalation/abc"}
    )
    cursor = await conn.execute(
        "SELECT output FROM invocation_log WHERE task_type = ?",
        (ESCALATION_TASK_TYPE,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["event"] == "escalation_submitted"
    assert payload["mode"] == "claude_code"


async def test_concurrent_submit_raises(conn: aiosqlite.Connection) -> None:
    await _seed(conn)
    # First submission succeeds.
    await submit_escalation_core(
        conn, "cc-1", {"mode": "claude_code", "branch": "x"}
    )
    # Mark row as something else to simulate concurrent state change
    # between SELECT and UPDATE — but the function checks status first,
    # so this fires NotAwaitingSubmissionError. The pure-race path is
    # exercised by the slice 19 integration test.
    with pytest.raises(NotAwaitingSubmissionError):
        await submit_escalation_core(
            conn, "cc-1", {"mode": "claude_code", "branch": "x"}
        )


# ConcurrentSubmissionError is unreachable through serial Python calls
# without a second writer — the pre-conditions are already covered by
# the slice 19 integration test that hits the FastAPI route directly.
# This sentinel ensures the import/symbol stays exported.
def test_concurrent_submission_error_export() -> None:
    assert issubclass(ConcurrentSubmissionError, Exception)
