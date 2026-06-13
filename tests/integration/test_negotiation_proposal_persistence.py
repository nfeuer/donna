"""Integration: negotiation_proposals table + repository round-trip.

Exercises the real Alembic-migrated schema and the Database repo methods so the
propose-and-confirm path (design §3) provably survives restarts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.fixture
async def db(tmp_path: Path, state_machine: StateMachine) -> Database:
    database = Database(tmp_path / "neg.db", state_machine)
    await database.connect()
    await database.run_migrations()
    return database


@pytest.mark.asyncio
async def test_proposal_round_trip_and_status(db: Database) -> None:
    now = datetime(2026, 6, 15, 8, tzinfo=UTC)
    moves = [
        {
            "task_id": "victim", "event_id": "ev1",
            "old_start": now.isoformat(), "old_end": (now + timedelta(hours=1)).isoformat(),
            "new_start": (now + timedelta(hours=1)).isoformat(),
            "new_end": (now + timedelta(hours=2)).isoformat(), "priority": 1,
        }
    ]
    await db.create_negotiation_proposal(
        proposal_id="p1",
        task_id="T",
        slot_start=now.isoformat(),
        slot_end=(now + timedelta(hours=1)).isoformat(),
        moves_json=json.dumps(moves),
        cost=42.0,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=4)).isoformat(),
    )

    row = await db.get_negotiation_proposal("p1")
    assert row is not None
    assert row["status"] == "pending"
    assert row["task_id"] == "T"
    assert row["cost"] == 42.0
    assert json.loads(row["moves_json"])[0]["task_id"] == "victim"

    await db.update_negotiation_proposal_status("p1", "accepted")
    row2 = await db.get_negotiation_proposal("p1")
    assert row2 is not None
    assert row2["status"] == "accepted"


@pytest.mark.asyncio
async def test_missing_proposal_returns_none(db: Database) -> None:
    assert await db.get_negotiation_proposal("nope") is None


@pytest.mark.asyncio
async def test_proposals_survive_reconnect(tmp_path: Path, state_machine: StateMachine) -> None:
    """A persisted proposal is readable after closing and reopening the DB."""
    db_path = tmp_path / "neg_persist.db"
    db = Database(db_path, state_machine)
    await db.connect()
    await db.run_migrations()
    now = datetime(2026, 6, 15, 8, tzinfo=UTC)
    await db.create_negotiation_proposal(
        proposal_id="p2", task_id="T2", slot_start=now.isoformat(),
        slot_end=(now + timedelta(hours=1)).isoformat(), moves_json="[]",
        cost=1.0, created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=4)).isoformat(),
    )
    await db.close()

    db2 = Database(db_path, state_machine)
    await db2.connect()
    row = await db2.get_negotiation_proposal("p2")
    assert row is not None and row["status"] == "pending"
    await db2.close()
