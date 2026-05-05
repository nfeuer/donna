"""Unit tests for crash-recovery scan (slice 18).

Tests the `_run_crash_recovery` coroutine from cli_wiring:
- Stale grants (granted + no real invocation) get voided.
- Completed invocations are not voided.
- Voiding writes an extension_voided audit event to invocation_log.

Realizes manual-escalation.md §10.6 row 4.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite
import pytest
import pytest_asyncio
import structlog

from donna.cli_wiring import _run_crash_recovery
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.escalation_repository import EscalationRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path):
    """Full schema needed by crash-recovery (escalation_request + extensions + invocation_log)."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as c:
        await c.execute("PRAGMA journal_mode=WAL")
        await c.execute(
            """
            CREATE TABLE escalation_request (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                correlation_id TEXT UNIQUE NOT NULL,
                task_id TEXT,
                task_type TEXT NOT NULL,
                estimate_usd REAL NOT NULL,
                daily_remaining_usd REAL NOT NULL,
                offered_modes TEXT NOT NULL,
                resolution TEXT,
                resolved_by TEXT,
                resolved_at TEXT,
                prompt_path TEXT,
                branch_name TEXT,
                iteration INTEGER DEFAULT 1,
                status TEXT DEFAULT 'open',
                created_at TEXT NOT NULL,
                submitted_at TEXT,
                validated_at TEXT,
                priority INTEGER DEFAULT 2,
                delivery_status TEXT,
                delivery_attempts INTEGER DEFAULT 0,
                last_delivery_attempt_at TEXT,
                parent_escalation_id INTEGER
            )
            """
        )
        await c.execute(
            """
            CREATE TABLE daily_budget_extension (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                granted_at TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                escalation_request_id INTEGER,
                voided INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (escalation_request_id)
                    REFERENCES escalation_request(id)
            )
            """
        )
        await c.execute(
            """
            CREATE UNIQUE INDEX ux_daily_budget_extension_idempotency
              ON daily_budget_extension (escalation_request_id, granted_by)
            """
        )
        await c.execute(
            """
            CREATE TABLE invocation_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                task_type TEXT NOT NULL,
                task_id TEXT,
                model_alias TEXT,
                model_actual TEXT,
                input_hash TEXT,
                latency_ms INTEGER,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                output TEXT,
                quality_score REAL,
                is_shadow INTEGER DEFAULT 0,
                eval_session_id TEXT,
                spot_check_queued INTEGER DEFAULT 0,
                user_id TEXT,
                queue_wait_ms INTEGER,
                interrupted INTEGER DEFAULT 0,
                chain_id TEXT,
                caller TEXT,
                estimated_tokens_in INTEGER,
                overflow_escalated INTEGER DEFAULT 0,
                skill_id TEXT,
                escalation_request_id INTEGER
            )
            """
        )
        await c.commit()
        yield c


async def _insert_escalation(
    conn, esc_id: int, correlation_id: str, resolution: str = "api_extended"
):
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO escalation_request
            (id, user_id, correlation_id, task_id, task_type, estimate_usd,
             daily_remaining_usd, offered_modes, resolution, status, created_at)
        VALUES (?, 'nick', ?, 'task-1', 'skill_draft', 2.50, 17.50, '[]', ?, 'resolved', ?)
        """,
        (esc_id, correlation_id, resolution, now),
    )
    await conn.commit()


async def _insert_extension(conn, esc_id: int):
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by, escalation_request_id, voided)
        VALUES ('nick', '2026-05-05', 2.50, ?, 'discord-123', ?, 0)
        """,
        (now, esc_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_recovery_voids_stale_grant(conn):
    """A stale grant (no invocation) is voided and audit event written."""
    await _insert_escalation(conn, 1, "corr-1")
    await _insert_extension(conn, 1)

    extension_repo = BudgetExtensionRepository(conn)
    escalation_repo = EscalationRepository(conn)
    log = structlog.get_logger()

    await _run_crash_recovery(
        extension_repo=extension_repo,
        escalation_repo=escalation_repo,
        conn=conn,
        user_id="nick",
        log=log,
    )

    # The extension should now be voided
    cursor = await conn.execute(
        "SELECT voided FROM daily_budget_extension WHERE escalation_request_id = 1"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1  # voided

    # An extension_voided audit row should have been written to invocation_log
    cursor = await conn.execute(
        """
        SELECT output FROM invocation_log
        WHERE task_type = 'escalation_lifecycle' AND escalation_request_id = 1
        """
    )
    audit_row = await cursor.fetchone()
    assert audit_row is not None
    payload = json.loads(audit_row[0])
    assert payload["event"] == "extension_voided"
    assert payload["reason"] == "crash_recovery"


@pytest.mark.asyncio
async def test_crash_recovery_skips_completed_invocation(conn):
    """An extension that has a real invocation log entry is not voided."""
    await _insert_escalation(conn, 2, "corr-2")
    await _insert_extension(conn, 2)

    # Insert a real invocation (not escalation_lifecycle)
    await conn.execute(
        """
        INSERT INTO invocation_log
            (id, task_type, escalation_request_id, user_id, cost_usd,
             tokens_in, tokens_out, latency_ms, is_shadow, spot_check_queued)
        VALUES ('inv-2', 'skill_draft', 2, 'nick', 0.001, 50, 20, 100, 0, 0)
        """
    )
    await conn.commit()

    extension_repo = BudgetExtensionRepository(conn)
    escalation_repo = EscalationRepository(conn)
    log = structlog.get_logger()

    await _run_crash_recovery(
        extension_repo=extension_repo,
        escalation_repo=escalation_repo,
        conn=conn,
        user_id="nick",
        log=log,
    )

    # Extension must remain NOT voided
    cursor = await conn.execute(
        "SELECT voided FROM daily_budget_extension WHERE escalation_request_id = 2"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0  # still active


@pytest.mark.asyncio
async def test_crash_recovery_skips_already_voided(conn):
    """An already-voided grant is not touched a second time."""
    await _insert_escalation(conn, 3, "corr-3")
    # Insert pre-voided extension
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by, escalation_request_id, voided)
        VALUES ('nick', '2026-05-05', 2.50, ?, 'discord-123', 3, 1)
        """,
        (now,),
    )
    await conn.commit()

    extension_repo = BudgetExtensionRepository(conn)
    escalation_repo = EscalationRepository(conn)
    log = structlog.get_logger()

    await _run_crash_recovery(
        extension_repo=extension_repo,
        escalation_repo=escalation_repo,
        conn=conn,
        user_id="nick",
        log=log,
    )

    # Should not write any new audit rows for esc_id=3
    cursor = await conn.execute(
        """
        SELECT COUNT(*) FROM invocation_log
        WHERE task_type = 'escalation_lifecycle' AND escalation_request_id = 3
        """
    )
    (count,) = await cursor.fetchone()
    assert count == 0


@pytest.mark.asyncio
async def test_crash_recovery_multiple_stale_all_voided(conn):
    """All stale grants in a batch are voided."""
    for i in (10, 11, 12):
        await _insert_escalation(conn, i, f"corr-{i}")
        await _insert_extension(conn, i)

    extension_repo = BudgetExtensionRepository(conn)
    escalation_repo = EscalationRepository(conn)
    log = structlog.get_logger()

    await _run_crash_recovery(
        extension_repo=extension_repo,
        escalation_repo=escalation_repo,
        conn=conn,
        user_id="nick",
        log=log,
    )

    for i in (10, 11, 12):
        cursor = await conn.execute(
            "SELECT voided FROM daily_budget_extension WHERE escalation_request_id = ?", (i,)
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == 1, f"esc_id={i} should be voided"


@pytest.mark.asyncio
async def test_crash_recovery_no_stale_grants_noop(conn):
    """With no stale grants, crash recovery runs without error and changes nothing."""
    extension_repo = BudgetExtensionRepository(conn)
    escalation_repo = EscalationRepository(conn)
    log = structlog.get_logger()

    # Should complete without raising
    await _run_crash_recovery(
        extension_repo=extension_repo,
        escalation_repo=escalation_repo,
        conn=conn,
        user_id="nick",
        log=log,
    )

    cursor = await conn.execute("SELECT COUNT(*) FROM daily_budget_extension")
    (count,) = await cursor.fetchone()
    assert count == 0
