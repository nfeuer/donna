"""Unit tests for EscalationDeliveryLoop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.cost.escalation_audit import ESCALATION_TASK_TYPE
from donna.cost.escalation_repository import EscalationRepository
from donna.notifications.escalation_delivery_loop import EscalationDeliveryLoop
from donna.tasks.db_models import TaskStatus

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
    skill_id TEXT,
    escalation_request_id INTEGER
);
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "loop.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


def _make_db(conn: aiosqlite.Connection) -> MagicMock:
    """Stand-in Database double — only ``connection`` and
    ``transition_task_state`` are used by the loop."""
    db = MagicMock()
    db.connection = conn
    db.transition_task_state = AsyncMock()
    return db


class TestRetryPendingDeliveries:
    async def test_succeeds_on_retry(
        self, conn: aiosqlite.Connection
    ) -> None:
        repo = EscalationRepository(conn)
        await repo.create(
            user_id="nick",
            correlation_id="c1",
            task_id="t1",
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause"],
            priority=2,
        )
        deliver = AsyncMock(side_effect=[False, True])
        loop = EscalationDeliveryLoop(
            db=_make_db(conn),
            repository=repo,
            timeout_minutes=60,
            deliver=deliver,
        )
        await loop.tick_once()
        await loop.tick_once()
        cursor = await conn.execute(
            "SELECT delivery_status, delivery_attempts FROM escalation_request"
        )
        row = await cursor.fetchone()
        assert row[0] == "sent"
        assert row[1] == 2


class TestSweepTimeouts:
    async def test_times_out_old_open_row(
        self, conn: aiosqlite.Connection
    ) -> None:
        repo = EscalationRepository(conn)
        await conn.execute(
            "INSERT INTO tasks (id, status) VALUES (?, ?)",
            ("t-old", TaskStatus.SCHEDULED.value),
        )
        await conn.commit()
        old_now = datetime.now(tz=UTC) - timedelta(minutes=120)
        await repo.create(
            user_id="nick",
            correlation_id="cold",
            task_id="t-old",
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause", "cancel"],
            priority=2,
            now=old_now,
        )

        sms_manager = MagicMock()
        sms_manager.escalate = AsyncMock()
        deliver = AsyncMock(return_value=True)
        loop = EscalationDeliveryLoop(
            db=_make_db(conn),
            repository=repo,
            timeout_minutes=60,
            deliver=deliver,
            sms_manager=sms_manager,
        )
        await loop.tick_once()

        cursor = await conn.execute(
            "SELECT status, resolution, resolved_by FROM escalation_request"
        )
        row = await cursor.fetchone()
        assert row == ("resolved", "pause", "timeout")

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM invocation_log WHERE task_type = ?",
            (ESCALATION_TASK_TYPE,),
        )
        (count,) = await cursor.fetchone()
        assert count == 1
        # Priority was 2 (< 4) → SMS not invoked.
        sms_manager.escalate.assert_not_called()

    async def test_high_priority_fires_sms(
        self, conn: aiosqlite.Connection
    ) -> None:
        repo = EscalationRepository(conn)
        await conn.execute(
            "INSERT INTO tasks (id, status) VALUES (?, ?)",
            ("t-hp", TaskStatus.IN_PROGRESS.value),
        )
        await conn.commit()
        old_now = datetime.now(tz=UTC) - timedelta(minutes=120)
        await repo.create(
            user_id="nick",
            correlation_id="chp",
            task_id="t-hp",
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause", "cancel"],
            priority=4,
            now=old_now,
        )

        sms_manager = MagicMock()
        sms_manager.escalate = AsyncMock()
        loop = EscalationDeliveryLoop(
            db=_make_db(conn),
            repository=repo,
            timeout_minutes=60,
            deliver=AsyncMock(return_value=True),
            sms_manager=sms_manager,
        )
        await loop.tick_once()
        sms_manager.escalate.assert_called_once()
        kwargs = sms_manager.escalate.call_args.kwargs
        assert kwargs["start_at_tier"] == 2
        assert kwargs["priority"] == 4


class TestDailyRefresh:
    async def test_paused_to_backlog_at_day_rollover(
        self, conn: aiosqlite.Connection
    ) -> None:
        repo = EscalationRepository(conn)
        await conn.execute(
            "INSERT INTO tasks (id, status) VALUES (?, ?)",
            ("t-p", TaskStatus.PAUSED.value),
        )
        await conn.commit()

        db = _make_db(conn)
        loop = EscalationDeliveryLoop(
            db=db,
            repository=repo,
            timeout_minutes=60,
            deliver=AsyncMock(return_value=True),
        )
        # First tick — primes the date watermark, no transition.
        await loop.tick_once(now=datetime(2026, 5, 5, 23, 50, tzinfo=UTC))
        db.transition_task_state.assert_not_called()
        # Second tick the next day — should refresh paused tasks.
        await loop.tick_once(now=datetime(2026, 5, 6, 0, 1, tzinfo=UTC))
        db.transition_task_state.assert_awaited_with("t-p", TaskStatus.BACKLOG)
