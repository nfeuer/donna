"""Unit tests for EscalationGate slice 18 additions.

Covers:
- `_should_offer_extension`: enabled flag, daily headroom, monthly ceiling
- `grant_budget_extension`: idempotent, ceiling enforced, audit event written
- `_daily_remaining`: extensions factored into effective cap
- `GateOutcome.extension_amount_usd` populated for api_extended resolution

Realizes manual-escalation.md §5.1, §6.1, §10.6.
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
    BudgetExtensionConfig,
    ManualEscalationConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.escalation_gate import DeliveryCallback, EscalationGate
from donna.cost.escalation_repository import (
    EscalationRepository,
)
from donna.cost.tracker import CostSummary

# ---------------------------------------------------------------------------
# Schema fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "gate_test.db"
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    enabled: bool = True,
    max_daily_extension_usd: float = 10.0,
    hard_monthly_ceiling_usd: float = 150.0,
    daily_pause_threshold_usd: float = 20.0,
) -> tuple[ManualEscalationConfig, float]:
    config = ManualEscalationConfig(
        enabled=enabled,
        modes=ManualEscalationModesConfig(),
        budget_extension=BudgetExtensionConfig(
            enabled=True,
            max_daily_extension_usd=max_daily_extension_usd,
            hard_monthly_ceiling_usd=hard_monthly_ceiling_usd,
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=5.0,
        ),
    )
    return config, daily_pause_threshold_usd


def _make_resolver(enabled: bool = True) -> MagicMock:
    resolver = MagicMock()
    resolver.get = AsyncMock(return_value=enabled)
    return resolver


def _make_tracker(daily_spent: float = 0.0) -> MagicMock:
    tracker = MagicMock()
    tracker.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_spent, call_count=1, breakdown={})
    )
    return tracker


async def _make_gate(
    conn: aiosqlite.Connection,
    *,
    daily_spent: float = 0.0,
    max_daily_extension_usd: float = 10.0,
    hard_monthly_ceiling_usd: float = 150.0,
    enabled: bool = True,
    deliver: DeliveryCallback | None = None,
) -> tuple[EscalationGate, BudgetExtensionRepository, EscalationRepository]:
    config, daily_pause = _make_config(
        enabled=enabled,
        max_daily_extension_usd=max_daily_extension_usd,
        hard_monthly_ceiling_usd=hard_monthly_ceiling_usd,
    )
    extension_repo = BudgetExtensionRepository(conn)
    escalation_repo = EscalationRepository(conn)

    async def _no_deliver(row: object) -> bool:
        return True

    gate = EscalationGate(
        repository=escalation_repo,
        tracker=_make_tracker(daily_spent=daily_spent),
        config=config,
        daily_pause_threshold_usd=daily_pause,
        resolver=_make_resolver(enabled=enabled),
        deliver=deliver or _no_deliver,
        extension_repo=extension_repo,
    )
    return gate, extension_repo, escalation_repo


async def _insert_escalation(
    conn: aiosqlite.Connection,
    esc_id: int,
    correlation_id: str,
    estimate_usd: float = 2.50,
) -> None:
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO escalation_request
            (id, user_id, correlation_id, task_id, task_type, estimate_usd,
             daily_remaining_usd, offered_modes, resolution, status, created_at)
        VALUES (?, 'nick', ?, 'task-1', 'skill_draft', ?, 17.50, '[]',
                'api_extended', 'resolved', ?)
        """,
        (esc_id, correlation_id, estimate_usd, now),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# _should_offer_extension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_offer_extension_basic(conn: aiosqlite.Connection) -> None:
    """api_extended renders when extension is enabled and there's enough headroom."""
    gate, _extension_repo, _ = await _make_gate(
        conn, max_daily_extension_usd=10.0, hard_monthly_ceiling_usd=150.0
    )
    result = await gate._should_offer_extension(2.50, "nick")
    assert result is True


@pytest.mark.asyncio
async def test_should_offer_extension_disabled_by_config(conn: aiosqlite.Connection) -> None:
    """When budget_extension.enabled=False via resolver, api_extended is not offered."""
    _, daily_pause = _make_config()
    config = ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(),
        budget_extension=BudgetExtensionConfig(enabled=True),  # YAML says True
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=5.0,
        ),
    )
    extension_repo = BudgetExtensionRepository(conn)
    resolver = MagicMock()
    # resolver overrides budget_extension.enabled to False (dashboard kill-switch)
    resolver.get = AsyncMock(return_value=False)

    gate = EscalationGate(
        repository=EscalationRepository(conn),
        tracker=_make_tracker(),
        config=config,
        daily_pause_threshold_usd=daily_pause,
        resolver=resolver,
        deliver=AsyncMock(return_value=True),
        extension_repo=extension_repo,
    )
    result = await gate._should_offer_extension(2.50, "nick")
    assert result is False


@pytest.mark.asyncio
async def test_should_offer_extension_headroom_too_small(conn: aiosqlite.Connection) -> None:
    """Estimate exceeds remaining daily headroom → api_extended not offered."""
    gate, _extension_repo, _ = await _make_gate(
        conn, max_daily_extension_usd=2.0  # only $2 headroom
    )
    # Requesting $2.50 when max_daily is $2.00
    result = await gate._should_offer_extension(2.50, "nick")
    assert result is False


@pytest.mark.asyncio
async def test_should_offer_extension_monthly_ceiling_reached(conn: aiosqlite.Connection) -> None:
    """Monthly ceiling already reached → api_extended not offered."""
    # Insert non-voided extensions totalling $149 for this month
    now = datetime.now(tz=UTC).isoformat()
    today = date.today()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by, escalation_request_id, voided)
        VALUES ('nick', ?, 149.0, ?, 'u1', NULL, 0)
        """,
        (today.isoformat(), now),
    )
    await conn.commit()

    gate, _, _ = await _make_gate(
        conn, hard_monthly_ceiling_usd=150.0
    )
    # Adding $2.50 would exceed $150 monthly ceiling
    result = await gate._should_offer_extension(2.50, "nick")
    assert result is False


@pytest.mark.asyncio
async def test_should_offer_extension_monthly_ceiling_not_yet_reached(
    conn: aiosqlite.Connection,
) -> None:
    """Monthly total well below ceiling → api_extended offered."""
    now = datetime.now(tz=UTC).isoformat()
    today = date.today()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by, escalation_request_id, voided)
        VALUES ('nick', ?, 5.0, ?, 'u1', NULL, 0)
        """,
        (today.isoformat(), now),
    )
    await conn.commit()

    gate, _, _ = await _make_gate(
        conn, max_daily_extension_usd=10.0, hard_monthly_ceiling_usd=150.0
    )
    result = await gate._should_offer_extension(2.50, "nick")
    assert result is True


# ---------------------------------------------------------------------------
# grant_budget_extension
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_budget_extension_creates_row(conn: aiosqlite.Connection) -> None:
    """grant_budget_extension creates a daily_budget_extension row."""
    await _insert_escalation(conn, 1, "corr-1", estimate_usd=2.50)
    gate, _extension_repo, _ = await _make_gate(conn)

    result = await gate.grant_budget_extension(
        correlation_id="corr-1", granted_by="discord-999"
    )
    assert result is not None
    assert result.amount_usd == pytest.approx(2.50)
    assert result.granted_by == "discord-999"


@pytest.mark.asyncio
async def test_grant_budget_extension_idempotent(conn: aiosqlite.Connection) -> None:
    """Second call with same args returns existing row, no duplicate."""
    await _insert_escalation(conn, 2, "corr-2")
    gate, _extension_repo, _ = await _make_gate(conn)

    first = await gate.grant_budget_extension(correlation_id="corr-2", granted_by="u1")
    second = await gate.grant_budget_extension(correlation_id="corr-2", granted_by="u1")

    assert first is not None
    assert second is not None
    assert first.id == second.id

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM daily_budget_extension WHERE escalation_request_id = 2"
    )
    row = await cursor.fetchone()
    assert row is not None
    (count,) = row
    assert count == 1


@pytest.mark.asyncio
async def test_grant_budget_extension_missing_escalation_returns_none(
    conn: aiosqlite.Connection,
) -> None:
    """Returns None when escalation row cannot be found."""
    gate, _, _ = await _make_gate(conn)
    result = await gate.grant_budget_extension(
        correlation_id="nonexistent-corr", granted_by="u1"
    )
    assert result is None


@pytest.mark.asyncio
async def test_grant_budget_extension_monthly_ceiling_blocks(conn: aiosqlite.Connection) -> None:
    """Returns None when granting would exceed monthly ceiling."""
    await _insert_escalation(conn, 3, "corr-3", estimate_usd=5.0)
    # Pre-fill $149 of monthly extensions
    now = datetime.now(tz=UTC).isoformat()
    today = date.today()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by, escalation_request_id, voided)
        VALUES ('nick', ?, 149.0, ?, 'other-user', NULL, 0)
        """,
        (today.isoformat(), now),
    )
    await conn.commit()

    gate, _, _ = await _make_gate(conn, hard_monthly_ceiling_usd=150.0)
    # Adding $5 would bring total to $154, exceeding $150 ceiling
    result = await gate.grant_budget_extension(correlation_id="corr-3", granted_by="u1")
    assert result is None


# ---------------------------------------------------------------------------
# _daily_remaining with extensions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_remaining_without_extensions(conn: aiosqlite.Connection) -> None:
    """Remaining = daily_pause_threshold - spent (no extensions)."""
    gate, _, _ = await _make_gate(conn, daily_spent=5.0)
    remaining = await gate._daily_remaining("nick")
    # 20.0 - 5.0 = 15.0
    assert remaining == pytest.approx(15.0)


@pytest.mark.asyncio
async def test_daily_remaining_with_extensions_raises_cap(conn: aiosqlite.Connection) -> None:
    """Approved extension raises the effective cap for remaining computation."""
    now = datetime.now(tz=UTC).isoformat()
    today = date.today()
    await conn.execute(
        """
        INSERT INTO daily_budget_extension
            (user_id, date, amount_usd, granted_at, granted_by, escalation_request_id, voided)
        VALUES ('nick', ?, 5.0, ?, 'u1', NULL, 0)
        """,
        (today.isoformat(), now),
    )
    await conn.commit()

    gate, _, _ = await _make_gate(conn, daily_spent=18.0)
    remaining = await gate._daily_remaining("nick")
    # Effective cap = 20 + 5 = 25; spent = 18; remaining = 7
    assert remaining == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_daily_remaining_never_negative(conn: aiosqlite.Connection) -> None:
    """Remaining is clamped to 0.0 when spend exceeds effective cap."""
    gate, _, _ = await _make_gate(conn, daily_spent=99.0)
    remaining = await gate._daily_remaining("nick")
    assert remaining == 0.0
