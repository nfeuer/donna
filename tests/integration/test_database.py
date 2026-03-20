"""Integration tests for Database class and InvocationLogger.

Uses tmp_path (file-based SQLite) with tables created from SQLAlchemy metadata.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine

from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import (
    Base,
    DeadlineType,
    InputChannel,
    TaskDomain,
    TaskStatus,
)
from donna.tasks.state_machine import InvalidTransitionError, StateMachine

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db(tmp_path, state_machine):
    """Create a Database backed by a temp file with tables from metadata."""
    db_path = tmp_path / "test.db"
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()

    # Create tables via SQLAlchemy metadata (faster than Alembic for tests).
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    yield database
    await database.close()


class TestDatabaseConnection:
    async def test_wal_mode_enabled(self, db: Database) -> None:
        cursor = await db.connection.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"

    async def test_foreign_keys_enabled(self, db: Database) -> None:
        cursor = await db.connection.execute("PRAGMA foreign_keys")
        row = await cursor.fetchone()
        assert row[0] == 1


class TestCreateTask:
    async def test_create_minimal_task(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Buy groceries")
        assert task.title == "Buy groceries"
        assert task.user_id == "nick"
        assert task.status == "backlog"
        assert task.domain == "personal"
        assert task.priority == 2

    async def test_create_task_with_all_fields(self, db: Database) -> None:
        deadline = datetime(2026, 4, 1, 12, 0)
        task = await db.create_task(
            user_id="nick",
            title="Ship feature",
            description="Deploy v2 to production",
            domain=TaskDomain.WORK,
            priority=1,
            status=TaskStatus.BACKLOG,
            estimated_duration=120,
            deadline=deadline,
            deadline_type=DeadlineType.HARD,
            scheduled_start=datetime(2026, 3, 30, 9, 0),
            created_via=InputChannel.SLACK,
            tags=["deploy", "urgent"],
            notes=["Needs QA sign-off"],
            prep_work_flag=True,
            prep_work_instructions="Run staging tests first",
            agent_eligible=True,
            estimated_cost=2.50,
        )
        assert task.domain == "work"
        assert task.priority == 1
        assert task.estimated_duration == 120
        assert task.deadline_type == "hard"
        assert task.created_via == "slack"
        assert task.prep_work_flag is True
        assert task.agent_eligible is True

    async def test_create_task_generates_uuid(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Test UUID")
        assert len(task.id) == 36  # Standard UUID format
        assert "-" in task.id

    async def test_create_task_default_status_is_backlog(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Check defaults")
        assert task.status == "backlog"


class TestGetTask:
    async def test_get_existing_task(self, db: Database) -> None:
        created = await db.create_task(user_id="nick", title="Find me")
        found = await db.get_task(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.title == "Find me"

    async def test_get_nonexistent_task_returns_none(self, db: Database) -> None:
        result = await db.get_task("00000000-0000-0000-0000-000000000000")
        assert result is None


class TestUpdateTask:
    async def test_update_title(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Original")
        updated = await db.update_task(task.id, title="Updated")
        assert updated is not None
        assert updated.title == "Updated"

    async def test_update_multiple_fields(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Multi-update")
        updated = await db.update_task(
            task.id, priority=1, description="Now has a description"
        )
        assert updated is not None
        assert updated.priority == 1
        assert updated.description == "Now has a description"

    async def test_update_nonexistent_task_returns_none(self, db: Database) -> None:
        result = await db.update_task(
            "00000000-0000-0000-0000-000000000000", title="Ghost"
        )
        assert result is None

    async def test_update_invalid_column_raises(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Bad update")
        with pytest.raises(ValueError, match="Invalid columns"):
            await db.update_task(task.id, nonexistent_col="value")


class TestListTasks:
    async def test_list_all_tasks_for_user(self, db: Database) -> None:
        await db.create_task(user_id="nick", title="Task 1")
        await db.create_task(user_id="nick", title="Task 2")
        await db.create_task(user_id="other", title="Task 3")

        tasks = await db.list_tasks(user_id="nick")
        assert len(tasks) == 2
        assert all(t.user_id == "nick" for t in tasks)

    async def test_list_tasks_filter_by_status(self, db: Database) -> None:
        await db.create_task(user_id="nick", title="Backlog task")
        t2 = await db.create_task(user_id="nick", title="Scheduled task")
        await db.update_task(t2.id, status="scheduled")

        tasks = await db.list_tasks(status=TaskStatus.BACKLOG)
        assert len(tasks) == 1
        assert tasks[0].status == "backlog"

    async def test_list_tasks_filter_by_domain(self, db: Database) -> None:
        await db.create_task(
            user_id="nick", title="Work task", domain=TaskDomain.WORK
        )
        await db.create_task(
            user_id="nick", title="Personal task", domain=TaskDomain.PERSONAL
        )

        tasks = await db.list_tasks(domain=TaskDomain.WORK)
        assert len(tasks) == 1
        assert tasks[0].domain == "work"

    async def test_list_tasks_filter_combined(self, db: Database) -> None:
        await db.create_task(
            user_id="nick", title="Work backlog", domain=TaskDomain.WORK
        )
        await db.create_task(
            user_id="nick",
            title="Personal backlog",
            domain=TaskDomain.PERSONAL,
        )

        tasks = await db.list_tasks(
            user_id="nick", domain=TaskDomain.WORK, status=TaskStatus.BACKLOG
        )
        assert len(tasks) == 1
        assert tasks[0].title == "Work backlog"

    async def test_list_tasks_empty_result(self, db: Database) -> None:
        tasks = await db.list_tasks(user_id="nobody")
        assert tasks == []


class TestTransitionTaskState:
    async def test_backlog_to_scheduled_returns_side_effects(
        self, db: Database
    ) -> None:
        task = await db.create_task(user_id="nick", title="Schedule me")
        effects = await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        assert "create_calendar_event" in effects

    async def test_backlog_to_done_raises_invalid(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Skip steps")
        with pytest.raises(InvalidTransitionError):
            await db.transition_task_state(task.id, TaskStatus.DONE)

    async def test_transition_updates_status_in_db(self, db: Database) -> None:
        task = await db.create_task(user_id="nick", title="Track status")
        await db.transition_task_state(task.id, TaskStatus.SCHEDULED)
        updated = await db.get_task(task.id)
        assert updated is not None
        assert updated.status == "scheduled"

    async def test_transition_nonexistent_task_raises_value_error(
        self, db: Database
    ) -> None:
        with pytest.raises(ValueError, match="Task not found"):
            await db.transition_task_state(
                "00000000-0000-0000-0000-000000000000", TaskStatus.SCHEDULED
            )


class TestInvocationLogger:
    @pytest.fixture
    def inv_logger(self, db: Database) -> InvocationLogger:
        return InvocationLogger(db.connection)

    async def test_log_invocation_writes_row(
        self, db: Database, inv_logger: InvocationLogger
    ) -> None:
        metadata = InvocationMetadata(
            task_type="task_capture",
            model_alias="primary",
            model_actual="claude-sonnet-4-20250514",
            input_hash="abc123",
            latency_ms=450,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.003,
            user_id="nick",
        )
        inv_id = await inv_logger.log(metadata)

        cursor = await db.connection.execute(
            "SELECT task_type, model_alias, latency_ms, cost_usd FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "task_capture"
        assert row[1] == "primary"
        assert row[2] == 450
        assert row[3] == pytest.approx(0.003)

    async def test_log_invocation_returns_id(
        self, inv_logger: InvocationLogger
    ) -> None:
        metadata = InvocationMetadata(
            task_type="test",
            model_alias="primary",
            model_actual="claude-sonnet-4-20250514",
            input_hash="def456",
            latency_ms=100,
            tokens_in=50,
            tokens_out=50,
            cost_usd=0.001,
            user_id="nick",
        )
        inv_id = await inv_logger.log(metadata)
        assert len(inv_id) == 36
        assert "-" in inv_id

    async def test_log_invocation_with_output_json(
        self, db: Database, inv_logger: InvocationLogger
    ) -> None:
        metadata = InvocationMetadata(
            task_type="task_capture",
            model_alias="primary",
            model_actual="claude-sonnet-4-20250514",
            input_hash="ghi789",
            latency_ms=300,
            tokens_in=80,
            tokens_out=150,
            cost_usd=0.002,
            user_id="nick",
            output={"title": "Buy milk", "priority": 2},
        )
        inv_id = await inv_logger.log(metadata)

        cursor = await db.connection.execute(
            "SELECT output FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        import json

        output = json.loads(row[0])
        assert output["title"] == "Buy milk"


class TestAlembicMigrations:
    def test_upgrade_head_creates_tables(self, tmp_path) -> None:
        """Run alembic upgrade head on a fresh DB and verify tables exist."""
        import sqlite3

        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "migration_test.db"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(str(db_path))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()

        assert "tasks" in tables
        assert "invocation_log" in tables
        assert "correction_log" in tables
        assert "learned_preferences" in tables
        assert "conversation_context" in tables

    def test_downgrade_removes_tables(self, tmp_path) -> None:
        """Run upgrade then downgrade, verify tables are gone."""
        import sqlite3

        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "downgrade_test.db"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")

        conn = sqlite3.connect(str(db_path))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()

        assert "tasks" not in tables
        assert "invocation_log" not in tables
