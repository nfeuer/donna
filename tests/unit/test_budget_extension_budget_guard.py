"""Unit tests for BudgetGuard + BudgetExtensionRepository integration (slice 18).

Verifies that `check_pre_call` factors in approved extensions when computing
the effective daily cap. Realizes manual-escalation.md §10.6 row 2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from donna.config import (
    CostConfig,
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
    QualityMonitoringConfig,
    RoutingEntry,
)
from donna.cost.budget import BudgetGuard, BudgetPausedError
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.tracker import CostSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_models_config(daily_pause: float = 20.0) -> ModelsConfig:
    return ModelsConfig(
        models={"parser": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514")},
        routing={"skill_draft": RoutingEntry(model="parser")},
        cost=CostConfig(
            daily_pause_threshold_usd=daily_pause,
            monthly_budget_usd=100.0,
            monthly_warning_pct=0.9,
        ),
        ollama=OllamaConfig(),
        quality_monitoring=QualityMonitoringConfig(),
    )


def _make_tracker(daily_spent: float = 0.0) -> MagicMock:
    tracker = MagicMock()
    tracker.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_spent, call_count=1, breakdown={})
    )
    tracker.get_monthly_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_spent, call_count=1, breakdown={})
    )
    return tracker


# ---------------------------------------------------------------------------
# Fixtures: real aiosqlite DB with budget_extension table
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as c:
        await c.execute("PRAGMA journal_mode=WAL")
        await c.execute(
            """
            CREATE TABLE escalation_request (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                correlation_id TEXT UNIQUE NOT NULL,
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
                parent_escalation_id INTEGER,
                human_review INTEGER NOT NULL DEFAULT 0,
                target_paths TEXT,
                originating_entity_type TEXT,
                originating_entity_id TEXT,
                base_sha TEXT,
                merged_at TEXT
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
        await c.commit()
        yield c


@pytest_asyncio.fixture
async def extension_repo(conn: aiosqlite.Connection) -> BudgetExtensionRepository:
    return BudgetExtensionRepository(conn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_pre_call_passes_when_under_base_limit() -> None:
    """No extension; spend < base limit → no error."""
    tracker = _make_tracker(daily_spent=5.0)
    guard = BudgetGuard(tracker=tracker, models_config=_make_models_config(daily_pause=20.0))
    # Should not raise
    await guard.check_pre_call(user_id="nick")


@pytest.mark.asyncio
async def test_check_pre_call_raises_when_over_base_limit() -> None:
    """No extension; spend >= base limit → BudgetPausedError."""
    tracker = _make_tracker(daily_spent=20.0)
    guard = BudgetGuard(tracker=tracker, models_config=_make_models_config(daily_pause=20.0))
    with pytest.raises(BudgetPausedError) as exc_info:
        await guard.check_pre_call(user_id="nick")
    assert exc_info.value.daily_spent == pytest.approx(20.0)
    assert exc_info.value.daily_limit == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_check_pre_call_extension_raises_effective_cap(
    conn: aiosqlite.Connection, extension_repo: BudgetExtensionRepository
) -> None:
    """Extension of 5.0 → effective cap = 25.0; spend=22.0 → passes."""
    # Insert a $5 extension
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by,
             escalation_request_id, voided)
        VALUES ('nick', ?, 5.0, ?, 'u1', NULL, 0)
        """,
        (date.today().isoformat(), now),
    )
    await conn.commit()

    tracker = _make_tracker(daily_spent=22.0)
    guard = BudgetGuard(
        tracker=tracker,
        models_config=_make_models_config(daily_pause=20.0),
        extension_repo=extension_repo,
    )
    # Effective cap = 20 + 5 = 25; spent = 22 → should pass
    await guard.check_pre_call(user_id="nick")


@pytest.mark.asyncio
async def test_check_pre_call_extension_still_raises_when_over(
    conn: aiosqlite.Connection, extension_repo: BudgetExtensionRepository
) -> None:
    """Extension raises cap but spend still exceeds it → BudgetPausedError."""
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by,
             escalation_request_id, voided)
        VALUES ('nick', ?, 5.0, ?, 'u1', NULL, 0)
        """,
        (date.today().isoformat(), now),
    )
    await conn.commit()

    tracker = _make_tracker(daily_spent=26.0)
    guard = BudgetGuard(
        tracker=tracker,
        models_config=_make_models_config(daily_pause=20.0),
        extension_repo=extension_repo,
    )
    # Effective cap = 25; spent = 26 → should raise
    with pytest.raises(BudgetPausedError) as exc_info:
        await guard.check_pre_call(user_id="nick")
    assert exc_info.value.daily_limit == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_check_pre_call_voided_extension_ignored(
    conn: aiosqlite.Connection, extension_repo: BudgetExtensionRepository
) -> None:
    """Voided extensions do not count toward the effective cap."""
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by,
             escalation_request_id, voided)
        VALUES ('nick', ?, 5.0, ?, 'u1', NULL, 1)
        """,
        (date.today().isoformat(), now),
    )
    await conn.commit()

    tracker = _make_tracker(daily_spent=20.0)
    guard = BudgetGuard(
        tracker=tracker,
        models_config=_make_models_config(daily_pause=20.0),
        extension_repo=extension_repo,
    )
    # Voided extension doesn't count; cap = 20; spent = 20 → raises
    with pytest.raises(BudgetPausedError):
        await guard.check_pre_call(user_id="nick")


@pytest.mark.asyncio
async def test_check_pre_call_extension_lookup_failure_uses_base_limit() -> None:
    """If extension_repo raises, fall back to the base limit gracefully."""
    tracker = _make_tracker(daily_spent=21.0)
    broken_repo = MagicMock()
    broken_repo.get_daily_total = AsyncMock(side_effect=RuntimeError("db error"))

    guard = BudgetGuard(
        tracker=tracker,
        models_config=_make_models_config(daily_pause=20.0),
        extension_repo=broken_repo,
    )
    # Falls back to base limit (20); spent = 21 → raises
    with pytest.raises(BudgetPausedError) as exc_info:
        await guard.check_pre_call(user_id="nick")
    # Limit reported is the base, not erroneously inflated
    assert exc_info.value.daily_limit == pytest.approx(20.0)
