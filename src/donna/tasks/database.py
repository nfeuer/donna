"""Async SQLite database access for Donna tasks.

Opens a single aiosqlite connection with WAL mode.
Runs Alembic migrations programmatically at startup.
See docs/task-system.md and CLAUDE.md for design rationale.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum as _enum_module
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog
import uuid6

from donna.chat.types import ChatMessage, ChatSession
from donna.tasks.db_models import (
    DeadlineType,
    InputChannel,
    TaskDomain,
    TaskStatus,
)
from donna.tasks.state_machine import StateMachine

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
    nudge_count: int
    quality_score: float | None
    # Wave 3: capability matched by intent dispatcher (nullable — claude-native)
    capability_name: str | None = None
    # Wave 3: extracted inputs dict from the intent dispatcher (parsed from JSON)
    inputs: dict[str, Any] | None = None


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
    "nudge_count",
    "quality_score",
    "capability_name",
    "inputs_json",
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
    "nudge_count",
    "quality_score",
    "capability_name",
    "inputs_json",
)

_SELECT_COLUMNS = ", ".join(_TASK_COLUMNS)

# Indexes of boolean columns in _TASK_COLUMNS (SQLite stores as 0/1).
_BOOL_INDEXES = {
    _TASK_COLUMNS.index("prep_work_flag"),
    _TASK_COLUMNS.index("agent_eligible"),
    _TASK_COLUMNS.index("donna_managed"),
}


_INPUTS_JSON_INDEX = _TASK_COLUMNS.index("inputs_json")


def _row_to_task(row: tuple[Any, ...]) -> TaskRow:
    """Map a raw SQLite row to a TaskRow, converting int booleans.

    The ``inputs_json`` column is the final SELECT column; it's parsed from
    JSON text into a dict and fed to ``TaskRow.inputs``. The ``capability_name``
    column maps 1:1.
    """
    values = list(row)
    for idx in _BOOL_INDEXES:
        values[idx] = bool(values[idx])
    # Parse inputs_json → inputs dict (swap in the parsed value at the same index).
    raw_inputs = values[_INPUTS_JSON_INDEX]
    if raw_inputs is not None:
        try:
            values[_INPUTS_JSON_INDEX] = json.loads(raw_inputs)
        except (TypeError, ValueError):
            values[_INPUTS_JSON_INDEX] = None
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
        from alembic.config import Config

        from alembic import command

        def _run() -> None:
            cfg = Config(
                str(self._alembic_config_path)
                if self._alembic_config_path
                else "alembic.ini"
            )
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
        capability_name: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> TaskRow:
        """Insert a new task and return it. Generates a uuid7 ID.

        ``capability_name`` + ``inputs`` are Wave 3 first-class columns written
        by the Discord intent dispatcher so downstream consumers can query
        structured task inputs. ``inputs`` is serialized to JSON at write-time
        and parsed back to a dict at read-time.
        """
        conn = self.connection
        task_id = str(uuid6.uuid7())
        now = datetime.utcnow().isoformat()

        await conn.execute(
            f"INSERT INTO tasks ({_SELECT_COLUMNS}) "
            f"VALUES ({', '.join('?' for _ in _TASK_COLUMNS)})",
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
                0,  # nudge_count
                None,  # quality_score
                capability_name,
                json.dumps(inputs) if inputs else None,
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
            if (
                key in ("tags", "notes", "dependencies") and isinstance(value, list)
            ) or (
                key == "inputs_json" and isinstance(value, dict)
            ):
                processed[key] = json.dumps(value)
            elif isinstance(value, datetime):
                processed[key] = value.isoformat()
            elif isinstance(value, _enum_module.Enum):
                processed[key] = value.value
            else:
                processed[key] = value

        set_clause = ", ".join(f"{col} = ?" for col in processed)
        values = [*list(processed.values()), task_id]

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

    async def increment_nudge_count(self, task_id: str) -> None:
        """Atomically increment nudge_count on a task."""
        conn = self.connection
        await conn.execute(
            "UPDATE tasks SET nudge_count = nudge_count + 1 WHERE id = ?",
            (task_id,),
        )
        await conn.commit()

    async def record_nudge_event(
        self,
        *,
        user_id: str,
        task_id: str,
        nudge_type: str,
        channel: str,
        message_text: str,
        llm_generated: bool = False,
        escalation_tier: int = 1,
    ) -> str:
        """Insert a nudge event and return its ID."""
        import uuid6

        conn = self.connection
        event_id = str(uuid6.uuid7())
        now = datetime.utcnow().isoformat()
        await conn.execute(
            """INSERT INTO nudge_events
               (id, user_id, task_id, nudge_type, channel, escalation_tier,
                message_text, llm_generated, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                user_id,
                task_id,
                nudge_type,
                channel,
                escalation_tier,
                message_text,
                llm_generated,
                now,
            ),
        )
        await conn.commit()
        logger.info(
            "nudge_event_recorded",
            event_id=event_id, task_id=task_id, nudge_type=nudge_type,
        )
        return event_id

    async def get_weekly_stats(self, user_id: str, since: datetime) -> dict[str, Any]:
        """Aggregate task and nudge stats for the weekly digest.

        Returns a dict with completion counts, nudge stats, and domain breakdown.
        """
        conn = self.connection
        since_iso = since.isoformat()

        # Tasks completed this period
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND completed_at >= ?",
            (user_id, since_iso),
        )
        tasks_completed = (await cursor.fetchone())[0]

        # Tasks created this period
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND created_at >= ?",
            (user_id, since_iso),
        )
        tasks_created = (await cursor.fetchone())[0]

        # Average time to complete (hours) for tasks completed this period
        cursor = await conn.execute(
            """SELECT AVG(
                 (julianday(completed_at) - julianday(created_at)) * 24
               ) FROM tasks
               WHERE user_id = ? AND completed_at >= ? AND completed_at IS NOT NULL""",
            (user_id, since_iso),
        )
        avg_hours_to_complete = (await cursor.fetchone())[0]

        # Most nudged tasks (top 5)
        cursor = await conn.execute(
            """SELECT id, title, nudge_count, reschedule_count, domain
               FROM tasks WHERE user_id = ? AND nudge_count > 0
               ORDER BY nudge_count DESC LIMIT 5""",
            (user_id,),
        )
        most_nudged = [
            {
                "id": r[0], "title": r[1], "nudge_count": r[2],
                "reschedule_count": r[3], "domain": r[4],
            }
            for r in await cursor.fetchall()
        ]

        # Most rescheduled tasks (top 5)
        cursor = await conn.execute(
            """SELECT id, title, reschedule_count, nudge_count, domain
               FROM tasks WHERE user_id = ? AND reschedule_count > 0
               ORDER BY reschedule_count DESC LIMIT 5""",
            (user_id,),
        )
        most_rescheduled = [
            {
                "id": r[0], "title": r[1], "reschedule_count": r[2],
                "nudge_count": r[3], "domain": r[4],
            }
            for r in await cursor.fetchall()
        ]

        # Domain breakdown
        cursor = await conn.execute(
            """SELECT domain,
                      COUNT(*) FILTER (WHERE completed_at >= ?) as completed,
                      COUNT(*) FILTER (
                          WHERE status IN (
                              'backlog','scheduled','in_progress','blocked','waiting_input'
                          )
                      ) as open_count,
                      AVG(nudge_count) as avg_nudges
               FROM tasks WHERE user_id = ?
               GROUP BY domain""",
            (since_iso, user_id),
        )
        domain_breakdown = {}
        for r in await cursor.fetchall():
            domain_breakdown[r[0]] = {
                "completed": r[1],
                "open": r[2],
                "avg_nudges": round(r[3] or 0, 1),
            }

        # Total nudges this period
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM nudge_events WHERE user_id = ? AND created_at >= ?",
            (user_id, since_iso),
        )
        total_nudges = (await cursor.fetchone())[0]

        # LLM cost this period (from invocation_log)
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log "
            "WHERE user_id = ? AND timestamp >= ?",
            (user_id, since_iso),
        )
        weekly_cost = round((await cursor.fetchone())[0], 2)

        return {
            "tasks_completed": tasks_completed,
            "tasks_created": tasks_created,
            "avg_hours_to_complete": (
                round(avg_hours_to_complete, 1) if avg_hours_to_complete else None
            ),
            "most_nudged": most_nudged,
            "most_rescheduled": most_rescheduled,
            "domain_breakdown": domain_breakdown,
            "total_nudges": total_nudges,
            "weekly_cost": weekly_cost,
            "completion_rate": (
                round(tasks_completed / tasks_created * 100, 1)
                if tasks_created > 0
                else 0
            ),
        }

    # ------------------------------------------------------------------
    # Chat session CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_chat_session(
        row: tuple[Any, ...], description: tuple[Any, ...]
    ) -> ChatSession:
        """Convert a SQLite row + cursor.description to a ChatSession."""
        col_names = [d[0] for d in description]
        data = dict(zip(col_names, row, strict=False))
        return ChatSession(
            id=data["id"],
            user_id=data["user_id"],
            channel=data["channel"],
            status=data["status"],
            created_at=data["created_at"],
            last_activity=data["last_activity"],
            expires_at=data["expires_at"],
            message_count=data["message_count"],
            pinned_task_id=data.get("pinned_task_id"),
            summary=data.get("summary"),
        )

    @staticmethod
    def _row_to_chat_message(
        row: tuple[Any, ...], description: tuple[Any, ...]
    ) -> ChatMessage:
        """Convert a SQLite row + cursor.description to a ChatMessage."""
        col_names = [d[0] for d in description]
        data = dict(zip(col_names, row, strict=False))
        return ChatMessage(
            id=data["id"],
            session_id=data["session_id"],
            role=data["role"],
            content=data["content"],
            created_at=data["created_at"],
            intent=data.get("intent"),
            tokens_used=data.get("tokens_used"),
        )

    async def create_chat_session(
        self,
        user_id: str,
        channel: str,
        ttl_minutes: int = 60,
    ) -> ChatSession:
        """Create a new active chat session. Returns the created ChatSession."""
        conn = self.connection
        session_id = str(uuid6.uuid7())
        now = datetime.utcnow()
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(minutes=ttl_minutes)).isoformat()

        await conn.execute(
            """INSERT INTO conversation_sessions
               (id, user_id, channel, status, created_at, last_activity, expires_at, message_count)
               VALUES (?, ?, ?, 'active', ?, ?, ?, 0)""",
            (session_id, user_id, channel, now_iso, now_iso, expires_iso),
        )
        await conn.commit()

        logger.info(
            "chat_session_created",
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )
        session = await self.get_chat_session(session_id)
        return session  # type: ignore[return-value]

    async def get_chat_session(self, session_id: str) -> ChatSession | None:
        """Fetch a chat session by ID. Returns None if not found."""
        conn = self.connection
        cursor = await conn.execute(
            "SELECT * FROM conversation_sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_chat_session(row, cursor.description)

    async def get_active_chat_session(
        self, user_id: str, channel: str
    ) -> ChatSession | None:
        """Find the most recent active session for user+channel.

        Returns None if no active unexpired session exists.
        """
        conn = self.connection
        now_iso = datetime.utcnow().isoformat()
        cursor = await conn.execute(
            """SELECT * FROM conversation_sessions
               WHERE user_id = ? AND channel = ? AND status = 'active' AND expires_at > ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (user_id, channel, now_iso),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_chat_session(row, cursor.description)

    async def update_chat_session(self, session_id: str, **kwargs: Any) -> None:
        """Update allowed fields on a chat session.

        Allowed fields: status, summary, pinned_task_id, last_activity,
        expires_at, message_count.
        """
        _allowed = {
            "status",
            "summary",
            "pinned_task_id",
            "last_activity",
            "expires_at",
            "message_count",
        }
        if not kwargs:
            return

        invalid = set(kwargs.keys()) - _allowed
        if invalid:
            raise ValueError(f"Invalid fields for update_chat_session: {invalid}")

        conn = self.connection
        set_clause = ", ".join(f"{col} = ?" for col in kwargs)
        values = [*list(kwargs.values()), session_id]

        await conn.execute(
            f"UPDATE conversation_sessions SET {set_clause} WHERE id = ?",
            values,
        )
        await conn.commit()

    async def get_expired_chat_sessions(self) -> list[ChatSession]:
        """Return active sessions whose expires_at is in the past."""
        conn = self.connection
        now_iso = datetime.utcnow().isoformat()
        cursor = await conn.execute(
            """SELECT * FROM conversation_sessions
               WHERE status = 'active' AND expires_at <= ?
               ORDER BY expires_at ASC""",
            (now_iso,),
        )
        rows = await cursor.fetchall()
        description = cursor.description
        return [self._row_to_chat_session(row, description) for row in rows]

    async def add_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        tokens_used: int | None = None,
    ) -> ChatMessage:
        """Insert a message and increment session message_count + last_activity."""
        conn = self.connection
        message_id = str(uuid6.uuid7())
        now_iso = datetime.utcnow().isoformat()

        await conn.execute(
            """INSERT INTO conversation_messages
               (id, session_id, role, content, intent, tokens_used, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (message_id, session_id, role, content, intent, tokens_used, now_iso),
        )
        await conn.execute(
            """UPDATE conversation_sessions
               SET message_count = message_count + 1, last_activity = ?
               WHERE id = ?""",
            (now_iso, session_id),
        )
        await conn.commit()

        logger.info(
            "chat_message_added",
            message_id=message_id,
            session_id=session_id,
            role=role,
        )

        cursor = await conn.execute(
            "SELECT * FROM conversation_messages WHERE id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_chat_message(row, cursor.description)

    async def list_chat_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessage]:
        """Return paginated messages for a session, ordered by created_at ASC."""
        conn = self.connection
        cursor = await conn.execute(
            """SELECT * FROM conversation_messages
               WHERE session_id = ?
               ORDER BY created_at ASC
               LIMIT ? OFFSET ?""",
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        description = cursor.description
        return [self._row_to_chat_message(row, description) for row in rows]
