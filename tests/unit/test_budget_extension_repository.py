"""Unit tests for BudgetExtensionRepository (slice 18).

Covers: grant idempotency, daily/monthly totals, void, and stale-grant
detection used by crash-recovery scan.

Realizes docs/superpowers/specs/manual-escalation.md §10.6.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import aiosqlite
import pytest
import pytest_asyncio

from donna.cost.budget_extension import BudgetExtensionRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path):
    """In-memory SQLite connection with the minimal schema for these tests."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as c:
        await c.execute("PRAGMA journal_mode=WAL")
        # escalation_request stub (FK target)
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
        # Idempotency index (mirrors migration d0e1f2a3b4c5)
        await c.execute(
            """
            CREATE UNIQUE INDEX ux_daily_budget_extension_idempotency
              ON daily_budget_extension (escalation_request_id, granted_by)
            """
        )
        # invocation_log stub (needed for find_stale_grants)
        await c.execute(
            """
            CREATE TABLE invocation_log (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                escalation_request_id INTEGER
            )
            """
        )
        await c.commit()
        yield c


@pytest_asyncio.fixture
async def repo(conn):
    return BudgetExtensionRepository(conn)


async def _insert_escalation(conn, esc_id: int, resolution: str = "api_extended"):
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO escalation_request
            (id, user_id, correlation_id, task_type, estimate_usd,
             daily_remaining_usd, offered_modes, resolution,
             status, created_at)
        VALUES (?, 'nick', ?, 'skill_draft', 2.50, 17.50, '[]', ?, 'resolved', ?)
        """,
        (esc_id, f"corr-{esc_id}", resolution, now),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# grant — basic happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_creates_row(repo, conn):
    await _insert_escalation(conn, 1)
    row = await repo.grant(
        user_id="nick",
        for_date=date(2026, 5, 5),
        amount_usd=2.50,
        granted_by="discord-123",
        escalation_request_id=1,
    )
    assert row is not None
    assert row.amount_usd == 2.50
    assert row.granted_by == "discord-123"
    assert row.voided is False


@pytest.mark.asyncio
async def test_grant_idempotent_same_key_returns_existing(repo, conn):
    await _insert_escalation(conn, 2)
    first = await repo.grant(
        user_id="nick",
        for_date=date(2026, 5, 5),
        amount_usd=2.50,
        granted_by="discord-123",
        escalation_request_id=2,
    )
    assert first is not None

    # Second call with same (escalation_request_id, granted_by) — must not
    # insert a new row; returns the existing row.
    second = await repo.grant(
        user_id="nick",
        for_date=date(2026, 5, 5),
        amount_usd=2.50,
        granted_by="discord-123",
        escalation_request_id=2,
    )
    assert second is not None
    assert second.id == first.id

    # Confirm only one row in the table.
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM daily_budget_extension WHERE escalation_request_id = 2"
    )
    (count,) = await cursor.fetchone()
    assert count == 1


# ---------------------------------------------------------------------------
# get_daily_total
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_daily_total_sums_non_voided(repo, conn):
    await _insert_escalation(conn, 3)
    await _insert_escalation(conn, 4)
    await _insert_escalation(conn, 5)

    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=2.0,
        granted_by="u1", escalation_request_id=3,
    )
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=3.0,
        granted_by="u2", escalation_request_id=4,
    )
    # Grant then void the third
    row3 = await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=1.5,
        granted_by="u3", escalation_request_id=5,
    )
    assert row3 is not None
    await repo.void_by_escalation_request_id(5)

    total = await repo.get_daily_total("nick", date(2026, 5, 5))
    # Only the two non-voided rows count.
    assert total == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_get_daily_total_zero_when_none(repo):
    total = await repo.get_daily_total("nick", date(2026, 5, 5))
    assert total == 0.0


# ---------------------------------------------------------------------------
# get_monthly_total
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_monthly_total(repo, conn):
    await _insert_escalation(conn, 6)
    await _insert_escalation(conn, 7)
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 1), amount_usd=4.0,
        granted_by="u1", escalation_request_id=6,
    )
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 31), amount_usd=3.0,
        granted_by="u2", escalation_request_id=7,
    )
    total = await repo.get_monthly_total("nick", 2026, 5)
    assert total == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_get_monthly_total_excludes_other_months(repo, conn):
    await _insert_escalation(conn, 8)
    await repo.grant(
        user_id="nick", for_date=date(2026, 4, 30), amount_usd=5.0,
        granted_by="u1", escalation_request_id=8,
    )
    total = await repo.get_monthly_total("nick", 2026, 5)
    assert total == 0.0


# ---------------------------------------------------------------------------
# void_by_escalation_request_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_void_returns_true_when_updated(repo, conn):
    await _insert_escalation(conn, 9)
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=2.0,
        granted_by="u1", escalation_request_id=9,
    )
    voided = await repo.void_by_escalation_request_id(9)
    assert voided is True


@pytest.mark.asyncio
async def test_void_returns_false_when_no_row(repo):
    voided = await repo.void_by_escalation_request_id(999)
    assert voided is False


@pytest.mark.asyncio
async def test_void_idempotent_second_call_returns_false(repo, conn):
    await _insert_escalation(conn, 10)
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=2.0,
        granted_by="u1", escalation_request_id=10,
    )
    await repo.void_by_escalation_request_id(10)
    second = await repo.void_by_escalation_request_id(10)
    assert second is False  # already voided


# ---------------------------------------------------------------------------
# find_stale_grants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_stale_grants_returns_unrun_escalation_ids(repo, conn):
    # Escalation 11: api_extended, extension granted, NO invocation_log row.
    await _insert_escalation(conn, 11)
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=2.0,
        granted_by="u1", escalation_request_id=11,
    )
    stale = await repo.find_stale_grants()
    assert 11 in stale


@pytest.mark.asyncio
async def test_find_stale_grants_excludes_completed(repo, conn):
    # Escalation 12: api_extended, extension granted, HAS a real invocation.
    await _insert_escalation(conn, 12)
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=2.0,
        granted_by="u1", escalation_request_id=12,
    )
    # Insert a real (non-escalation_lifecycle) invocation log row
    await conn.execute(
        """
        INSERT INTO invocation_log (id, task_type, escalation_request_id)
        VALUES ('inv-abc', 'skill_draft', 12)
        """
    )
    await conn.commit()

    stale = await repo.find_stale_grants()
    assert 12 not in stale


@pytest.mark.asyncio
async def test_find_stale_grants_excludes_voided(repo, conn):
    # Escalation 13: already voided (previous crash recovery ran).
    await _insert_escalation(conn, 13)
    await repo.grant(
        user_id="nick", for_date=date(2026, 5, 5), amount_usd=2.0,
        granted_by="u1", escalation_request_id=13,
    )
    await repo.void_by_escalation_request_id(13)

    stale = await repo.find_stale_grants()
    assert 13 not in stale


@pytest.mark.asyncio
async def test_find_stale_grants_excludes_non_api_extended(repo, conn):
    # Escalation 14: resolution = 'pause', not api_extended.
    await _insert_escalation(conn, 14, resolution="pause")
    # Even if a row exists in daily_budget_extension (shouldn't happen, but
    # test the query filter).
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by,
             escalation_request_id, voided)
        VALUES ('nick', '2026-05-05', 2.0, '2026-05-05T10:00:00', 'u1', 14, 0)
        """
    )
    await conn.commit()

    stale = await repo.find_stale_grants()
    assert 14 not in stale
