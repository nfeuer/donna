"""Tests for CostTracker — cost aggregation from invocation_log."""

from __future__ import annotations

from datetime import date, datetime

import aiosqlite
import pytest

from donna.cost.tracker import CostTracker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_conn():
    """In-memory SQLite connection with invocation_log schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(
        """CREATE TABLE invocation_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            task_type TEXT NOT NULL,
            task_id TEXT,
            model_alias TEXT NOT NULL,
            model_actual TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            output TEXT,
            quality_score REAL,
            is_shadow INTEGER DEFAULT 0,
            eval_session_id TEXT,
            spot_check_queued INTEGER DEFAULT 0,
            user_id TEXT NOT NULL,
            skill_id TEXT
        )"""
    )
    await conn.commit()
    yield conn
    await conn.close()


async def _insert_row(
    conn: aiosqlite.Connection,
    *,
    row_id: str,
    timestamp: str,
    task_type: str,
    model_alias: str,
    cost_usd: float,
    user_id: str = "nick",
) -> None:
    await conn.execute(
        """INSERT INTO invocation_log
           (id, timestamp, task_type, model_alias, model_actual, input_hash,
            latency_ms, tokens_in, tokens_out, cost_usd, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row_id, timestamp, task_type, model_alias, "claude-sonnet-4-20250514",
         "abc123", 200, 100, 50, cost_usd, user_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetDailyCost:
    async def test_sums_correctly(self, db_conn: aiosqlite.Connection) -> None:
        await _insert_row(
            db_conn, row_id="1", timestamp="2026-03-20T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.0010,
        )
        await _insert_row(
            db_conn, row_id="2", timestamp="2026-03-20T14:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.0020,
        )
        await _insert_row(
            db_conn, row_id="3", timestamp="2026-03-20T22:00:00",
            task_type="dedup_check", model_alias="parser", cost_usd=0.0005,
        )
        tracker = CostTracker(db_conn)
        summary = await tracker.get_daily_cost(for_date=date(2026, 3, 20))

        assert abs(summary.total_usd - 0.0035) < 1e-9
        assert summary.call_count == 3

    async def test_excludes_other_days(self, db_conn: aiosqlite.Connection) -> None:
        await _insert_row(
            db_conn, row_id="1", timestamp="2026-03-19T23:59:59",
            task_type="parse_task", model_alias="parser", cost_usd=5.00,
        )
        await _insert_row(
            db_conn, row_id="2", timestamp="2026-03-20T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.50,
        )
        await _insert_row(
            db_conn, row_id="3", timestamp="2026-03-21T00:00:01",
            task_type="parse_task", model_alias="parser", cost_usd=5.00,
        )
        tracker = CostTracker(db_conn)
        summary = await tracker.get_daily_cost(for_date=date(2026, 3, 20))

        assert abs(summary.total_usd - 0.50) < 1e-9
        assert summary.call_count == 1

    async def test_empty_log_returns_zero(self, db_conn: aiosqlite.Connection) -> None:
        tracker = CostTracker(db_conn)
        summary = await tracker.get_daily_cost(for_date=date(2026, 3, 20))
        assert summary.total_usd == 0.0
        assert summary.call_count == 0
        assert summary.breakdown == {}

    async def test_breakdown_by_task_type(self, db_conn: aiosqlite.Connection) -> None:
        await _insert_row(
            db_conn, row_id="1", timestamp="2026-03-20T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.10,
        )
        await _insert_row(
            db_conn, row_id="2", timestamp="2026-03-20T11:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.10,
        )
        await _insert_row(
            db_conn, row_id="3", timestamp="2026-03-20T12:00:00",
            task_type="dedup_check", model_alias="parser", cost_usd=0.05,
        )
        tracker = CostTracker(db_conn)
        summary = await tracker.get_daily_cost(for_date=date(2026, 3, 20))

        assert abs(summary.breakdown["parse_task"] - 0.20) < 1e-9
        assert abs(summary.breakdown["dedup_check"] - 0.05) < 1e-9


class TestGetMonthlyCost:
    async def test_sums_full_month(self, db_conn: aiosqlite.Connection) -> None:
        # Rows in January
        await _insert_row(
            db_conn, row_id="1", timestamp="2026-01-05T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=1.00,
        )
        await _insert_row(
            db_conn, row_id="2", timestamp="2026-01-31T23:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=2.00,
        )
        # Row in February — should not be included
        await _insert_row(
            db_conn, row_id="3", timestamp="2026-02-01T00:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=99.0,
        )
        tracker = CostTracker(db_conn)
        summary = await tracker.get_monthly_cost(year=2026, month=1)

        assert abs(summary.total_usd - 3.00) < 1e-9
        assert summary.call_count == 2

    async def test_empty_month_returns_zero(self, db_conn: aiosqlite.Connection) -> None:
        tracker = CostTracker(db_conn)
        summary = await tracker.get_monthly_cost(year=2026, month=6)
        assert summary.total_usd == 0.0


class TestGetCostByTaskType:
    async def test_groups_correctly(self, db_conn: aiosqlite.Connection) -> None:
        await _insert_row(
            db_conn, row_id="1", timestamp="2026-03-15T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.50,
        )
        await _insert_row(
            db_conn, row_id="2", timestamp="2026-03-15T11:00:00",
            task_type="dedup_check", model_alias="parser", cost_usd=0.25,
        )
        await _insert_row(
            db_conn, row_id="3", timestamp="2026-03-16T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.50,
        )
        tracker = CostTracker(db_conn)
        result = await tracker.get_cost_by_task_type(date(2026, 3, 15), date(2026, 3, 16))

        assert abs(result["parse_task"] - 1.00) < 1e-9
        assert abs(result["dedup_check"] - 0.25) < 1e-9


class TestGetCostByAgent:
    async def test_groups_by_model_alias(self, db_conn: aiosqlite.Connection) -> None:
        await _insert_row(
            db_conn, row_id="1", timestamp="2026-03-20T10:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.30,
        )
        await _insert_row(
            db_conn, row_id="2", timestamp="2026-03-20T11:00:00",
            task_type="prep_research", model_alias="reasoner", cost_usd=0.70,
        )
        await _insert_row(
            db_conn, row_id="3", timestamp="2026-03-20T12:00:00",
            task_type="parse_task", model_alias="parser", cost_usd=0.20,
        )
        tracker = CostTracker(db_conn)
        result = await tracker.get_cost_by_agent(date(2026, 3, 20), date(2026, 3, 20))

        assert abs(result["parser"] - 0.50) < 1e-9
        assert abs(result["reasoner"] - 0.70) < 1e-9


class TestGetProjectedMonthlySpend:
    async def test_projection_uses_7_day_average(self, db_conn: aiosqlite.Connection) -> None:
        """Insert $7 over 7 days → $1/day average → projection = $1 × days_in_month."""
        import calendar as _cal
        from datetime import date, timedelta

        today = date.today()
        for i in range(7):
            day = today - timedelta(days=i)
            ts = datetime(day.year, day.month, day.day, 12, 0, 0).isoformat()
            await _insert_row(
                db_conn,
                row_id=f"row-{i}",
                timestamp=ts,
                task_type="parse_task",
                model_alias="parser",
                cost_usd=1.00,
            )

        tracker = CostTracker(db_conn)
        projected = await tracker.get_projected_monthly_spend()

        _, days_in_month = _cal.monthrange(today.year, today.month)
        expected = 1.0 * days_in_month
        assert abs(projected - expected) < 0.01

    async def test_empty_log_projects_zero(self, db_conn: aiosqlite.Connection) -> None:
        tracker = CostTracker(db_conn)
        projected = await tracker.get_projected_monthly_spend()
        assert projected == 0.0
