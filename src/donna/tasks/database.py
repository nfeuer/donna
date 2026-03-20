"""Async SQLite database access for Donna tasks.

Opens a single aiosqlite connection with WAL mode.
Runs Alembic migrations programmatically at startup.
See docs/task-system.md and CLAUDE.md for design rationale.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import enum as _enum_module

import aiosqlite
import structlog
import uuid6

from donna.tasks.db_models import (
    DeadlineType,
    InputChannel,
    TaskDomain,
    TaskStatus,
)
from donna.tasks.state_machine import StateMachine

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from donna.integrations.supabase_sync import SupabaseSync

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class TaskRow:
    """Lightweight read-only projection of a task row."""

    id: str
    user_id: str
    title: str
    description: str | None
    domain: str
    priority: int
    status: str
    estimated_duration: int | None
    deadline: str | None
    deadline_type: str
    scheduled_start: str | None
    actual_start: str | None
    completed_at: str | None
    recurrence: str | None
    dependencies: str | None
    parent_task: str | None
    prep_work_flag: bool
    prep_work_instructions: str | None
    agent_eligible: bool
    assigned_agent: str | None
    agent_status: str | None
    tags: str | None
    notes: str | None
    reschedule_count: int
    created_at: str
    created_via: str
    estimated_cost: float | None
    calendar_event_id: str | None
    donna_managed: bool


# Columns that can be updated via update_task().
_UPDATABLE_COLUMNS: set[str] = {
    "title",
    "description",
    "domain",
    "priority",
    "status",
    "estimated_duration",
    "deadline",
    "deadline_type",
    "scheduled_start",
    "actual_start",
    "completed_at",
    "recurrence",
    "dependencies",
    "parent_task",
    "prep_work_flag",
    "prep_work_instructions",
    "agent_eligible",
    "assigned_agent",
    "agent_status",
    "tags",
    "notes",
    "reschedule_count",
    "estimated_cost",
    "calendar_event_id",
    "donna_managed",
}

# Column order for SELECT — must match TaskRow field order.
_TASK_COLUMNS = (
    "id",
    "user_id",
    "title",
    "description",
    "domain",
    "priority",
    "status",
    "estimated_duration",
    "deadline",
    "deadline_type",
    "scheduled_start",
    "actual_start",
    "completed_at",
    "recurrence",
    "dependencies",
    "parent_task",
    "prep_work_flag",
    "prep_work_instructions",
    "agent_eligible",
    "assigned_agent",
    "agent_status",
    "tags",
    "notes",
    "reschedule_count",
    "created_at",
    "created_via",
    "estimated_cost",
    "calendar_event_id",
    "donna_managed",
)

_SELECT_COLUMNS = ", ".join(_TASK_COLUMNS)

# Indexes of boolean columns in _TASK_COLUMNS (SQLite stores as 0/1).
_BOOL_INDEXES = {
    _TASK_COLUMNS.index("prep_work_flag"),
    _TASK_COLUMNS.index("agent_eligible"),
    _TASK_COLUMNS.index("donna_managed"),
}


def _row_to_task(row: tuple[Any, ...]) -> TaskRow:
    """Map a raw SQLite row to a TaskRow, converting int booleans."""
    values = list(row)
    for idx in _BOOL_INDEXES:
        values[idx] = bool(values[idx])
    return TaskRow(*values)


class Database:
    """Async SQLite database access for Donna tasks.

    Opens a single aiosqlite connection with WAL mode.
    Runs Alembic migrations on startup.
    """

    def __init__(
        self,
        db_path: str | Path,
        state_machine: StateMachine,
        alembic_config_path: str | Path | None = None,
        supabase_sync: SupabaseSync | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._state_machine = state_machine
        self._alembic_config_path = alembic_config_path
        self._conn: aiosqlite.Connection | None = None
        self._supabase_sync = supabase_sync

    async def connect(self) -> None:
        """Open the aiosqlite connection and enable WAL mode."""
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        logger.info("database_connected", db_path=str(self._db_path))

    async def close(self) -> None:
        """Close the aiosqlite connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("database_closed")

    @property
    def connection(self) -> aiosqlite.Connection:
        """Expose the raw connection for the invocation logger."""
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    async def run_migrations(self) -> None:
        """Run alembic upgrade head programmatically."""
        from alembic import command
        from alembic.config import Config

        def _run() -> None:
            cfg = Config(str(self._alembic_config_path) if self._alembic_config_path else "alembic.ini")
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self._db_path}")
            command.upgrade(cfg, "head")

        await asyncio.to_thread(_run)
        logger.info("migrations_applied", db_path=str(self._db_path))

    async def create_task(
        self,
        user_id: str,
        title: str,
        description: str | None = None,
        domain: TaskDomain = TaskDomain.PERSONAL,
        priority: int = 2,
        status: TaskStatus = TaskStatus.BACKLOG,
        estimated_duration: int | None = None,
        deadline: datetime | None = None,
        deadline_type: DeadlineType = DeadlineType.NONE,
        scheduled_start: datetime | None = None,
        created_via: InputChannel = InputChannel.DISCORD,
        tags: list[str] | None = None,
        notes: list[str] | None = None,
        parent_task: str | None = None,
        dependencies: list[str] | None = None,
        prep_work_flag: bool = False,
        prep_work_instructions: str | None = None,
        agent_eligible: bool = False,
        estimated_cost: float | None = None,
    ) -> TaskRow:
        """Insert a new task and return it. Generates a uuid7 ID."""
        conn = self.connection
        task_id = str(uuid6.uuid7())
        now = datetime.utcnow().isoformat()

        await conn.execute(
            f"INSERT INTO tasks ({_SELECT_COLUMNS}) VALUES ({', '.join('?' for _ in _TASK_COLUMNS)})",
            (
                task_id,
                user_id,
                title,
                description,
                domain.value,
                priority,
                status.value,
                estimated_duration,
                deadline.isoformat() if deadline else None,
                deadline_type.value,
                scheduled_start.isoformat() if scheduled_start else None,
                None,  # actual_start
                None,  # completed_at
                None,  # recurrence
                json.dumps(dependencies) if dependencies else None,
                parent_task,
                prep_work_flag,
                prep_work_instructions,
                agent_eligible,
                None,  # assigned_agent
                None,  # agent_status
                json.dumps(tags) if tags else None,
                json.dumps(notes) if notes else None,
                0,  # reschedule_count
                now,
                created_via.value,
                estimated_cost,
                None,  # calendar_event_id
                False,  # donna_managed
            ),
        )
        await conn.commit()

        logger.info("task_created", task_id=task_id, title=title, user_id=user_id)
        task_row = await self.get_task(task_id)
        if self._supabase_sync is not None and task_row is not None:
            import dataclasses
            await self._supabase_sync.push_task(dataclasses.asdict(task_row))
        return task_row  # type: ignore[return-value]

    async def get_task(self, task_id: str) -> TaskRow | None:
        """Retrieve a single task by ID. Returns None if not found."""
        conn = self.connection
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_task(row)

    async def update_task(self, task_id: str, **fields: Any) -> TaskRow | None:
        """Update specific fields on a task. Returns updated row or None."""
        if not fields:
            return await self.get_task(task_id)

        invalid = set(fields.keys()) - _UPDATABLE_COLUMNS
        if invalid:
            raise ValueError(f"Invalid columns for update: {invalid}")

        conn = self.connection

        # Serialize special types
        processed: dict[str, Any] = {}
        for key, value in fields.items():
            if key in ("tags", "notes", "dependencies") and isinstance(value, list):
                processed[key] = json.dumps(value)
            elif isinstance(value, datetime):
                processed[key] = value.isoformat()
            elif isinstance(value, _enum_module.Enum):
                processed[key] = value.value
            else:
                processed[key] = value

        set_clause = ", ".join(f"{col} = ?" for col in processed)
        values = list(processed.values()) + [task_id]

        cursor = await conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            values,
        )
        await conn.commit()

        if cursor.rowcount == 0:
            return None

        task_row = await self.get_task(task_id)
        if self._supabase_sync is not None and task_row is not None:
            import dataclasses
            await self._supabase_sync.push_task(dataclasses.asdict(task_row))
        return task_row

    async def list_tasks(
        self,
        user_id: str | None = None,
        status: TaskStatus | None = None,
        domain: TaskDomain | None = None,
    ) -> list[TaskRow]:
        """List tasks with optional filters."""
        conn = self.connection
        where_clauses: list[str] = []
        params: list[Any] = []

        if user_id is not None:
            where_clauses.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            where_clauses.append("status = ?")
            params.append(status.value)
        if domain is not None:
            where_clauses.append("domain = ?")
            params.append(domain.value)

        query = f"SELECT {_SELECT_COLUMNS} FROM tasks"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY created_at DESC"

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_task(row) for row in rows]

    async def transition_task_state(
        self,
        task_id: str,
        new_status: TaskStatus,
    ) -> list[str]:
        """Validate and apply a state transition.

        Uses StateMachine.validate_transition().
        Returns the list of side effect names.
        Raises InvalidTransitionError on invalid transition.
        Raises ValueError if task not found.
        """
        conn = self.connection
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        side_effects = self._state_machine.validate_transition(
            task.status, new_status.value
        )

        await conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (new_status.value, task_id),
        )
        await conn.commit()

        logger.info(
            "task_state_transitioned",
            task_id=task_id,
            from_state=task.status,
            to_state=new_status.value,
            side_effects=side_effects,
        )

        return side_effects
