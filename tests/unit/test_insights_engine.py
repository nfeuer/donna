"""Tests for the insights engine — cost, quality, and token analysis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from donna.insights.engine import compute_insights

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE invocation_log (
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
)
"""


@pytest.fixture
async def db_conn():
    """In-memory SQLite with invocation_log schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


def _ts(days_ago: int = 1) -> str:
    """Return an ISO timestamp *days_ago* days before now (UTC)."""
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


async def _insert(
    conn: aiosqlite.Connection,
    *,
    row_id: str,
    task_type: str = "parse_task",
    tokens_in: int = 500,
    tokens_out: int = 100,
    cost_usd: float = 0.001,
    input_hash: str = "abc123",
    quality_score: float | None = None,
    is_shadow: int = 0,
    timestamp: str | None = None,
) -> None:
    ts = timestamp or _ts(1)
    await conn.execute(
        """INSERT INTO invocation_log
           (id, timestamp, task_type, model_alias, model_actual, input_hash,
            latency_ms, tokens_in, tokens_out, cost_usd, quality_score,
            is_shadow, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row_id, ts, task_type, "router", "claude-sonnet-4-20250514",
            input_hash, 200, tokens_in, tokens_out, cost_usd, quality_score,
            is_shadow, "nick",
        ),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Top cost centres
# ---------------------------------------------------------------------------


class TestTopCostCenters:
    async def test_returns_ranked_by_cost(self, db_conn: aiosqlite.Connection) -> None:
        await _insert(db_conn, row_id="1", task_type="parse_task", cost_usd=0.05)
        await _insert(db_conn, row_id="2", task_type="parse_task", cost_usd=0.03)
        await _insert(db_conn, row_id="3", task_type="digest", cost_usd=0.10)

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        centres = result["top_cost_centers"]

        assert len(centres) == 2
        # digest should be first (higher total cost).
        assert centres[0]["task_type"] == "digest"
        assert centres[0]["total_cost"] == pytest.approx(0.10)
        assert centres[0]["call_count"] == 1
        # parse_task second.
        assert centres[1]["task_type"] == "parse_task"
        assert centres[1]["total_cost"] == pytest.approx(0.08)
        assert centres[1]["call_count"] == 2

    async def test_excludes_shadow_rows(self, db_conn: aiosqlite.Connection) -> None:
        await _insert(db_conn, row_id="1", task_type="parse_task", cost_usd=0.05)
        await _insert(
            db_conn, row_id="2", task_type="parse_task",
            cost_usd=0.10, is_shadow=1,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        centres = result["top_cost_centers"]

        assert len(centres) == 1
        assert centres[0]["total_cost"] == pytest.approx(0.05)

    async def test_excludes_old_rows(self, db_conn: aiosqlite.Connection) -> None:
        await _insert(db_conn, row_id="1", task_type="parse_task", cost_usd=0.05)
        await _insert(
            db_conn, row_id="2", task_type="old_task",
            cost_usd=0.20, timestamp=_ts(days_ago=30),
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        centres = result["top_cost_centers"]

        assert len(centres) == 1
        assert centres[0]["task_type"] == "parse_task"

    async def test_avg_tokens(self, db_conn: aiosqlite.Connection) -> None:
        await _insert(
            db_conn, row_id="1", task_type="parse_task",
            tokens_in=400, tokens_out=80, cost_usd=0.01,
        )
        await _insert(
            db_conn, row_id="2", task_type="parse_task",
            tokens_in=600, tokens_out=120, cost_usd=0.01,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        c = result["top_cost_centers"][0]
        assert c["avg_tokens_in"] == 500
        assert c["avg_tokens_out"] == 100


# ---------------------------------------------------------------------------
# System prompt groups
# ---------------------------------------------------------------------------


class TestSystemPromptGroups:
    async def test_groups_by_hash(self, db_conn: aiosqlite.Connection) -> None:
        for i in range(6):
            await _insert(
                db_conn, row_id=str(i), input_hash="hash_a",
                tokens_in=1000, cost_usd=0.01,
            )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        groups = result["system_prompt_groups"]

        assert len(groups) == 1
        g = groups[0]
        assert g["hash"] == "hash_a"
        assert g["call_count"] == 6
        assert g["avg_tokens_in"] == 1000
        # 7-day window, 7 days look-back => factor = 1.0
        assert g["estimated_weekly_cost"] == pytest.approx(0.06)
        assert g["sample_invocation_id"] == "0"

    async def test_ignores_low_frequency(self, db_conn: aiosqlite.Connection) -> None:
        """Groups with fewer than 5 calls are excluded."""
        for i in range(4):
            await _insert(
                db_conn, row_id=str(i), input_hash="rare",
                cost_usd=0.01,
            )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        assert result["system_prompt_groups"] == []


# ---------------------------------------------------------------------------
# Quality/cost mismatches
# ---------------------------------------------------------------------------


class TestQualityCostMismatches:
    async def test_flags_high_cost_low_quality(
        self, db_conn: aiosqlite.Connection,
    ) -> None:
        # One cheap + good task type.
        await _insert(
            db_conn, row_id="1", task_type="cheap_good",
            cost_usd=0.001, quality_score=0.9,
        )
        # One expensive + bad task type.
        await _insert(
            db_conn, row_id="2", task_type="expensive_bad",
            cost_usd=0.10, quality_score=0.3,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        mismatches = result["quality_cost_mismatches"]

        assert len(mismatches) == 1
        m = mismatches[0]
        assert m["task_type"] == "expensive_bad"
        assert m["avg_quality_score"] == pytest.approx(0.3)

    async def test_no_mismatches_when_quality_is_good(
        self, db_conn: aiosqlite.Connection,
    ) -> None:
        await _insert(
            db_conn, row_id="1", task_type="parse_task",
            cost_usd=0.05, quality_score=0.8,
        )
        await _insert(
            db_conn, row_id="2", task_type="digest",
            cost_usd=0.05, quality_score=0.9,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        assert result["quality_cost_mismatches"] == []


# ---------------------------------------------------------------------------
# Token bloat outliers
# ---------------------------------------------------------------------------


class TestTokenBloatOutliers:
    async def test_finds_outliers(self, db_conn: aiosqlite.Connection) -> None:
        # Build a set of "normal" rows so the median is well-defined.
        for i in range(5):
            await _insert(
                db_conn, row_id=f"norm-{i}", task_type="parse_task",
                tokens_in=500, cost_usd=0.01,
            )
        # One outlier at 3x the median.
        await _insert(
            db_conn, row_id="outlier-1", task_type="parse_task",
            tokens_in=1500, cost_usd=0.05,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        outliers = result["token_bloat_outliers"]

        assert len(outliers) == 1
        o = outliers[0]
        assert o["invocation_id"] == "outlier-1"
        assert o["task_type"] == "parse_task"
        assert o["tokens_in"] == 1500
        assert o["median_for_type"] == 500
        assert o["ratio"] == 3.0
        assert o["cost_usd"] == pytest.approx(0.05)

    async def test_ratio_calculation(self, db_conn: aiosqlite.Connection) -> None:
        """Verify ratio is tokens_in / median."""
        for i in range(7):
            await _insert(
                db_conn, row_id=f"n-{i}", task_type="digest",
                tokens_in=200, cost_usd=0.005,
            )
        await _insert(
            db_conn, row_id="big", task_type="digest",
            tokens_in=1000, cost_usd=0.03,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        outliers = result["token_bloat_outliers"]

        assert len(outliers) == 1
        assert outliers[0]["ratio"] == 5.0

    async def test_no_outliers_when_all_similar(
        self, db_conn: aiosqlite.Connection,
    ) -> None:
        for i in range(5):
            await _insert(
                db_conn, row_id=str(i), task_type="parse_task",
                tokens_in=500, cost_usd=0.01,
            )

        result = await compute_insights(db_conn, payload_dir=None, days=7)
        assert result["token_bloat_outliers"] == []


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


class TestEmptyResults:
    async def test_empty_database(self, db_conn: aiosqlite.Connection) -> None:
        result = await compute_insights(db_conn, payload_dir=None, days=7)

        assert result["top_cost_centers"] == []
        assert result["system_prompt_groups"] == []
        assert result["quality_cost_mismatches"] == []
        assert result["token_bloat_outliers"] == []

    async def test_only_shadow_rows(self, db_conn: aiosqlite.Connection) -> None:
        await _insert(
            db_conn, row_id="1", task_type="parse_task",
            cost_usd=0.10, is_shadow=1,
        )

        result = await compute_insights(db_conn, payload_dir=None, days=7)

        assert result["top_cost_centers"] == []
        assert result["system_prompt_groups"] == []
        assert result["quality_cost_mismatches"] == []
        assert result["token_bloat_outliers"] == []
