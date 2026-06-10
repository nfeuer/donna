"""create_task stores time_intent and derives deadline/deadline_type from it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from donna.scheduling.time_intent import TimeIntent
from donna.tasks.database import Database
from donna.tasks.db_models import Base, DeadlineType

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db(tmp_path, state_machine):
    """Database backed by a temp file, tables from SQLAlchemy metadata."""
    db_path = tmp_path / "test.db"
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield database
    await database.close()


async def test_create_task_derives_deadline_from_window_intent(db: Database):
    ti = TimeIntent(kind="window", latest=datetime(2026, 6, 13, tzinfo=UTC), strictness="soft")
    task = await db.create_task(
        user_id="nick", title="Send invoices", time_intent_json=ti.to_json()
    )
    assert task.deadline is not None and task.deadline.startswith("2026-06-13")
    assert task.deadline_type == DeadlineType.SOFT.value
    assert task.time_intent_json == ti.to_json()


async def test_create_task_none_intent_leaves_deadline_type_none(db: Database):
    ti = TimeIntent(kind="none")
    task = await db.create_task(
        user_id="nick", title="Organize garage", time_intent_json=ti.to_json()
    )
    assert task.deadline is None
    assert task.deadline_type == DeadlineType.NONE.value
