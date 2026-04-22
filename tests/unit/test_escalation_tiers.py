"""Unit tests for EscalationManager.

Tests escalation tier advancement, acknowledgment, backoff, and budget-alert
fast-path — all using an in-memory SQLite database (no real Twilio).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SmsConfig, SmsEscalationConfig
from donna.notifications.escalation import (
    STATUS_ACKNOWLEDGED,
    STATUS_BACKED_OFF,
    EscalationManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_conn():
    """In-memory SQLite connection with escalation_state table."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE escalation_state (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_title TEXT NOT NULL,
            current_tier INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            next_escalation_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _make_config(tier1_wait: int = 30, tier2_wait: int = 60, backoff_hours: int = 2) -> SmsConfig:
    return SmsConfig(
        escalation=SmsEscalationConfig(
            tier1_wait_minutes=tier1_wait,
            tier2_wait_minutes=tier2_wait,
            busy_backoff_hours=backoff_hours,
        )
    )


def _make_manager(db_conn, config: SmsConfig | None = None) -> tuple[EscalationManager, MagicMock, MagicMock]:
    config = config or _make_config()

    mock_db = MagicMock()
    mock_db.connection = db_conn

    mock_service = MagicMock()
    mock_service.dispatch = AsyncMock(return_value=True)

    mock_sms = MagicMock()
    mock_sms.send = AsyncMock(return_value=True)

    manager = EscalationManager(
        db=mock_db,
        service=mock_service,
        sms=mock_sms,
        sms_config=config,
        user_id="u1",
        user_phone="+15555550001",
    )
    return manager, mock_service, mock_sms


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEscalateStartsTier1:
    async def test_escalate_tier1_sends_discord(self, db_conn) -> None:
        manager, mock_service, mock_sms = _make_manager(db_conn)

        await manager.escalate("task-1", "Buy milk", "You haven't done Buy milk!", priority=2)

        mock_service.dispatch.assert_called_once()
        mock_sms.send.assert_not_called()

    async def test_escalate_is_noop_if_already_pending(self, db_conn) -> None:
        manager, mock_service, _ = _make_manager(db_conn)

        await manager.escalate("task-1", "Buy milk", "nudge", priority=2)
        await manager.escalate("task-1", "Buy milk", "nudge again", priority=2)

        # dispatch only called once — second call is a no-op.
        assert mock_service.dispatch.call_count == 1

    async def test_budget_alert_starts_at_tier2(self, db_conn) -> None:
        manager, mock_service, mock_sms = _make_manager(db_conn)

        await manager.escalate(
            "task-2", "Budget exceeded", "Budget alert!", priority=5, start_at_tier=2
        )

        mock_sms.send.assert_called_once()
        mock_service.dispatch.assert_not_called()


class TestAdvanceTier:
    async def test_advance_to_tier2_after_timeout(self, db_conn) -> None:
        manager, _mock_service, mock_sms = _make_manager(db_conn)

        # Start escalation at Tier 1.
        await manager.escalate("task-1", "Buy milk", "nudge", priority=2)

        # Manually backdate next_escalation_at to simulate timeout.
        past = (datetime.now(tz=UTC) - timedelta(minutes=60)).isoformat()
        await db_conn.execute(
            "UPDATE escalation_state SET next_escalation_at = ? WHERE task_id = ?",
            (past, "task-1"),
        )
        await db_conn.commit()

        # advance_due should send SMS.
        await manager._advance_due()

        mock_sms.send.assert_called_once()
        # Check tier updated to 2.
        cursor = await db_conn.execute(
            "SELECT current_tier FROM escalation_state WHERE task_id = ?", ("task-1",)
        )
        row = await cursor.fetchone()
        assert row[0] == 2

    async def test_advance_stops_at_max_tier(self, db_conn) -> None:
        manager, _, _mock_sms = _make_manager(db_conn)

        # Start at tier 2 (already SMS-sent), backdate next_escalation_at.
        await manager.escalate("task-1", "Task", "nudge", priority=2, start_at_tier=2)

        past = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        await db_conn.execute(
            "UPDATE escalation_state SET next_escalation_at = ?, current_tier = 2 WHERE task_id = ?",
            (past, "task-1"),
        )
        await db_conn.commit()

        # Advance from tier 2 → tier 3 (email escalation, now active in Slice 8).
        # Tier 3 sends an email and stays pending for Tier 4.
        await manager._advance_due()

        cursor = await db_conn.execute(
            "SELECT status, current_tier FROM escalation_state WHERE task_id = ?", ("task-1",)
        )
        row = await cursor.fetchone()
        # Tier 3 is now active: escalation stays pending at tier 3.
        assert row[0] == "pending"
        assert row[1] == 3


class TestAcknowledge:
    async def test_acknowledge_resets_escalation(self, db_conn) -> None:
        manager, _, _ = _make_manager(db_conn)

        await manager.escalate("task-1", "Buy milk", "nudge", priority=2)
        await manager.acknowledge("task-1")

        cursor = await db_conn.execute(
            "SELECT status FROM escalation_state WHERE task_id = ?", ("task-1",)
        )
        row = await cursor.fetchone()
        assert row[0] == STATUS_ACKNOWLEDGED

    async def test_advance_does_not_fire_after_acknowledge(self, db_conn) -> None:
        manager, _, mock_sms = _make_manager(db_conn)

        await manager.escalate("task-1", "Buy milk", "nudge", priority=2)
        await manager.acknowledge("task-1")

        # Backdate — but status is acknowledged, so advance should skip it.
        past = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        await db_conn.execute(
            "UPDATE escalation_state SET next_escalation_at = ? WHERE task_id = ?",
            (past, "task-1"),
        )
        await db_conn.commit()

        await manager._advance_due()

        mock_sms.send.assert_not_called()


class TestBackoff:
    async def test_backoff_delays_escalation(self, db_conn) -> None:
        manager, _, _ = _make_manager(db_conn, _make_config(backoff_hours=2))

        await manager.escalate("task-1", "Buy milk", "nudge", priority=2)
        before = datetime.now(tz=UTC)
        await manager.backoff("task-1")

        cursor = await db_conn.execute(
            "SELECT status, next_escalation_at FROM escalation_state WHERE task_id = ?",
            ("task-1",),
        )
        row = await cursor.fetchone()
        assert row[0] == STATUS_BACKED_OFF

        new_next = datetime.fromisoformat(row[1]).replace(tzinfo=UTC)
        # next_escalation_at should be roughly 2 hours from now.
        assert new_next >= before + timedelta(hours=1, minutes=50)

    async def test_advance_after_backoff_fires_when_due(self, db_conn) -> None:
        manager, _, mock_sms = _make_manager(db_conn)

        await manager.escalate("task-1", "Task", "nudge", priority=2)
        await manager.backoff("task-1")

        # Backdate past the backoff window.
        past = (datetime.now(tz=UTC) - timedelta(hours=3)).isoformat()
        await db_conn.execute(
            "UPDATE escalation_state SET next_escalation_at = ? WHERE task_id = ?",
            (past, "task-1"),
        )
        await db_conn.commit()

        await manager._advance_due()

        mock_sms.send.assert_called_once()
