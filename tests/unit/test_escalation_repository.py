"""Unit tests for EscalationRepository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from donna.cost.escalation_repository import (
    DELIVERY_FAILED,
    DELIVERY_SENT,
    EscalationRepository,
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
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "esc.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


class TestCreate:
    async def test_returns_row_with_open_status(self, repo: EscalationRepository) -> None:
        row = await repo.create(
            user_id="nick",
            correlation_id="corr-1",
            task_id="task-1",
            task_type="skill_draft",
            estimate_usd=7.5,
            daily_remaining_usd=2.0,
            offered_modes=["pause", "cancel"],
            priority=3,
        )
        assert row.id > 0
        assert row.status == "open"
        assert row.offered_modes == ["pause", "cancel"]
        assert row.delivery_status == "pending"
        assert row.delivery_attempts == 0
        assert row.priority == 3

    async def test_correlation_id_is_unique(self, repo: EscalationRepository) -> None:
        await repo.create(
            user_id="nick",
            correlation_id="dup",
            task_id=None,
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=1.0,
            offered_modes=["pause"],
            priority=2,
        )
        with pytest.raises(Exception):
            await repo.create(
                user_id="nick",
                correlation_id="dup",
                task_id=None,
                task_type="x",
                estimate_usd=1.0,
                daily_remaining_usd=1.0,
                offered_modes=["pause"],
                priority=2,
            )


class TestResolve:
    async def test_first_resolve_wins(self, repo: EscalationRepository) -> None:
        row = await repo.create(
            user_id="nick",
            correlation_id="r1",
            task_id="t1",
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause", "cancel"],
            priority=2,
        )
        a = await repo.resolve(row.id, resolution="pause", resolved_by="user")
        assert a is True
        b = await repo.resolve(row.id, resolution="cancel", resolved_by="user")
        assert b is False
        final = await repo.get(row.id)
        assert final is not None
        assert final.resolution == "pause"
        assert final.status == "resolved"


class TestDeliveryAttempts:
    async def test_increments_and_records_status(
        self, repo: EscalationRepository
    ) -> None:
        row = await repo.create(
            user_id="nick",
            correlation_id="d1",
            task_id=None,
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause"],
            priority=2,
        )
        await repo.mark_delivery_attempt(row.id, delivery_status=DELIVERY_FAILED)
        await repo.mark_delivery_attempt(row.id, delivery_status=DELIVERY_SENT)
        final = await repo.get(row.id)
        assert final is not None
        assert final.delivery_status == "sent"
        assert final.delivery_attempts == 2


class TestListQueries:
    async def test_pending_and_failed_listed(
        self, repo: EscalationRepository
    ) -> None:
        # one pending, one failed, one sent → only the first two should list.
        for cid, ds in (("p1", None), ("p2", DELIVERY_FAILED), ("p3", DELIVERY_SENT)):
            row = await repo.create(
                user_id="nick",
                correlation_id=cid,
                task_id=None,
                task_type="x",
                estimate_usd=1.0,
                daily_remaining_usd=0.0,
                offered_modes=["pause"],
                priority=2,
            )
            if ds is not None:
                await repo.mark_delivery_attempt(row.id, delivery_status=ds)
        rows = await repo.list_open_pending_delivery()
        assert {r.correlation_id for r in rows} == {"p1", "p2"}

    async def test_timeout_listing(self, repo: EscalationRepository) -> None:
        now = datetime.now(tz=UTC)
        old = await repo.create(
            user_id="nick",
            correlation_id="old",
            task_id=None,
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause"],
            priority=2,
            now=now - timedelta(minutes=120),
        )
        new = await repo.create(
            user_id="nick",
            correlation_id="new",
            task_id=None,
            task_type="x",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause"],
            priority=2,
            now=now,
        )
        rows = await repo.list_open_past_timeout(timeout_minutes=60, now=now)
        assert {r.correlation_id for r in rows} == {"old"}
        # The new row is not yet timed out; sanity check the helper isn't broken.
        assert any(r.id == new.id for r in [await repo.get(new.id)])


class TestDashboardSetting:
    async def test_upsert_and_get(self, repo: EscalationRepository) -> None:
        await repo.upsert_dashboard_setting("flag.x", True)
        assert await repo.get_dashboard_setting("flag.x") is True
        await repo.upsert_dashboard_setting("flag.x", False)
        assert await repo.get_dashboard_setting("flag.x") is False

    async def test_missing_returns_none(
        self, repo: EscalationRepository
    ) -> None:
        assert await repo.get_dashboard_setting("nope") is None
