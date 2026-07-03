"""Integration test: update_task with source -> correction_log row."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from donna.preferences.correction_subscriber import CorrectionSubscriber
from donna.tasks.database import Database
from donna.tasks.db_models import Base
from donna.tasks.events import TaskEventBus

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def wired_db(tmp_path, state_machine):
    """Database with event bus and CorrectionSubscriber wired up."""
    db_path = tmp_path / "test.db"
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    bus = TaskEventBus()
    database.set_event_bus(bus)

    subscriber = CorrectionSubscriber(database)
    bus.subscribe("task_updated", subscriber.on_task_updated)

    yield database
    await database.close()


class TestCorrectionEventFlow:
    async def test_user_update_creates_correction_row(self, wired_db: Database) -> None:
        """A user-sourced update_task call produces a correction_log row."""
        task = await wired_db.create_task(
            user_id="nick", title="Test task", priority=3,
        )

        await wired_db.update_task(task.id, source="api", priority=5)

        cursor = await wired_db.connection.execute(
            "SELECT task_type, field_corrected, original_value, corrected_value "
            "FROM correction_log WHERE task_id = ?",
            (task.id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "api"            # task_type
        assert rows[0][1] == "priority"        # field_corrected
        assert rows[0][2] == "3"               # original_value
        assert rows[0][3] == "5"               # corrected_value

    async def test_system_update_creates_no_correction(self, wired_db: Database) -> None:
        """A system update (source=None) produces no correction_log row."""
        task = await wired_db.create_task(
            user_id="nick", title="System task", priority=2,
        )

        await wired_db.update_task(task.id, priority=4)

        cursor = await wired_db.connection.execute(
            "SELECT COUNT(*) FROM correction_log WHERE task_id = ?",
            (task.id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_non_learnable_field_not_logged(self, wired_db: Database) -> None:
        """A status change (not learnable) produces no correction row.

        Status writes now flow through the state machine
        (``transition_task_state``), which never emits the correction event —
        so a status change can't be mistaken for a learnable preference edit.
        """
        from donna.tasks.db_models import TaskStatus

        task = await wired_db.create_task(
            user_id="nick", title="Status task",
        )

        await wired_db.transition_task_state(task.id, TaskStatus.SCHEDULED)

        cursor = await wired_db.connection.execute(
            "SELECT COUNT(*) FROM correction_log WHERE task_id = ?",
            (task.id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_multi_field_update_logs_each(self, wired_db: Database) -> None:
        """Editing priority and domain in one call logs two corrections."""
        from donna.tasks.db_models import TaskDomain

        task = await wired_db.create_task(
            user_id="nick", title="Multi", priority=2,
        )

        await wired_db.update_task(
            task.id,
            source="discord_modal",
            priority=4,
            domain=TaskDomain.WORK,
        )

        cursor = await wired_db.connection.execute(
            "SELECT field_corrected FROM correction_log WHERE task_id = ? "
            "ORDER BY field_corrected",
            (task.id,),
        )
        rows = await cursor.fetchall()
        fields = [r[0] for r in rows]
        assert "domain" in fields
        assert "priority" in fields
        assert len(rows) == 2
