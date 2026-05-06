"""Slice 20 tests for ChatEscalationIngestionPoller.

Realizes the ingestion contract from
``docs/superpowers/specs/manual-escalation.md`` §5.2 + §10.10.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
import uuid6
from sqlalchemy import create_engine

from donna.skills.chat_escalation_ingestion_poller import (
    ChatEscalationIngestionPoller,
)
from donna.tasks.database import Database
from donna.tasks.db_models import Base, TaskStatus


async def _seed_task(conn: aiosqlite.Connection) -> str:
    """Insert a minimal task row directly so tests don't pay the
    create_task path's serialisation/observer overhead."""
    task_id = str(uuid6.uuid7())
    await conn.execute(
        """
        INSERT INTO tasks (
            id, user_id, title, description, domain, priority, status,
            deadline_type, created_at, created_via, prep_work_flag,
            agent_eligible, reschedule_count, donna_managed, nudge_count
        )
        VALUES (?, 'nick', 'Test task', NULL, 'personal', 2, 'backlog',
                'none', '2026-05-06T00:00:00', 'discord', 0,
                0, 0, 0, 0)
        """,
        (task_id,),
    )
    await conn.commit()
    return task_id


@pytest.fixture
async def db(tmp_path: Path, state_machine):
    db_path = tmp_path / "ingestion.db"
    # ``Base.metadata.create_all`` covers tasks + escalation_request +
    # invocation_log so the ingestion path has every table it needs.
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()
    yield database
    await database.close()


async def _seed_submitted_row(
    db: Database,
    *,
    correlation_id: str,
    task_id: str,
    answer: str,
    iteration: int = 1,
) -> int:
    cursor = await db.connection.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_id, task_type,
            estimate_usd, daily_remaining_usd, offered_modes,
            iteration, status, created_at, priority,
            delivery_status, delivery_attempts,
            mode, submitted_at, result
        )
        VALUES (
            'nick', ?, ?, 'chat_escalation',
            7.5, 1.0, '["chat","pause","cancel"]',
            ?, 'submitted', '2026-05-06T00:00:00+00:00', 2,
            'sent', 1,
            'chat', '2026-05-06T00:01:00+00:00', ?
        )
        """,
        (
            correlation_id,
            task_id,
            iteration,
            json.dumps({"mode": "chat", "answer": answer}),
        ),
    )
    await db.connection.commit()
    new_id = cursor.lastrowid
    assert new_id is not None
    return int(new_id)


class TestIngestion:
    async def test_appends_answer_marks_done_and_validates(
        self, db: Database
    ) -> None:
        task_id = await _seed_task(db.connection)
        answer = "x" * 60
        await _seed_submitted_row(
            db,
            correlation_id="cid-1",
            task_id=task_id,
            answer=answer,
        )

        poller = ChatEscalationIngestionPoller(db=db)
        processed = await poller.tick_once()
        assert processed == 1

        # Task ended up with the annotated answer in notes + status DONE.
        refreshed = await db.get_task(task_id)
        assert refreshed is not None
        assert refreshed.status == TaskStatus.DONE.value
        notes = json.loads(refreshed.notes) if refreshed.notes else []
        assert any(answer in n for n in notes)
        assert any(n.startswith("[escalation:cid-1]") for n in notes)

        # Escalation row is now validated with the channel marker.
        cur = await db.connection.execute(
            "SELECT status, validation_result, validated_at "
            "FROM escalation_request WHERE correlation_id = ?",
            ("cid-1",),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "validated"
        result = json.loads(row[1])
        assert result["channel"] == "chat"
        assert row[2] is not None

        # Audit log row was written.
        cur = await db.connection.execute(
            "SELECT COUNT(*) FROM invocation_log "
            "WHERE task_type = 'escalation_lifecycle' "
            "AND output LIKE '%escalation_validated%'"
        )
        count = (await cur.fetchone())[0]
        assert count == 1

    async def test_skips_rows_with_malformed_result(
        self, db: Database
    ) -> None:
        task_id = await _seed_task(db.connection)
        # Insert with a non-JSON result.
        await db.connection.execute(
            """
            INSERT INTO escalation_request (
                user_id, correlation_id, task_id, task_type,
                estimate_usd, daily_remaining_usd, offered_modes,
                iteration, status, created_at, priority,
                delivery_status, delivery_attempts,
                mode, submitted_at, result
            )
            VALUES (
                'nick', 'bad-1', ?, 'chat_escalation',
                1.0, 0.0, '[]', 1, 'submitted',
                '2026-05-06T00:00:00+00:00', 2, 'sent', 1,
                'chat', '2026-05-06T00:01:00+00:00', 'NOT JSON'
            )
            """,
            (task_id,),
        )
        await db.connection.commit()

        poller = ChatEscalationIngestionPoller(db=db)
        processed = await poller.tick_once()
        assert processed == 0
        # Row remains in submitted state — the next tick can still try.
        cur = await db.connection.execute(
            "SELECT status FROM escalation_request WHERE correlation_id = 'bad-1'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "submitted"

    async def test_idempotent_when_run_twice(self, db: Database) -> None:
        task_id = await _seed_task(db.connection)
        answer = "y" * 60
        await _seed_submitted_row(
            db,
            correlation_id="cid-twice",
            task_id=task_id,
            answer=answer,
        )

        poller = ChatEscalationIngestionPoller(db=db)
        first = await poller.tick_once()
        second = await poller.tick_once()
        assert first == 1
        assert second == 0

    async def test_skips_rows_without_task_id(self, db: Database) -> None:
        # A row whose task_id is NULL — ingestion has nowhere to land
        # the answer, so it should leave the row alone.
        await db.connection.execute(
            """
            INSERT INTO escalation_request (
                user_id, correlation_id, task_id, task_type,
                estimate_usd, daily_remaining_usd, offered_modes,
                iteration, status, created_at, priority,
                delivery_status, delivery_attempts,
                mode, submitted_at, result
            )
            VALUES (
                'nick', 'nullt', NULL, 'chat_escalation',
                1.0, 0.0, '[]', 1, 'submitted',
                '2026-05-06T00:00:00+00:00', 2, 'sent', 1,
                'chat', '2026-05-06T00:01:00+00:00', ?
            )
            """,
            (json.dumps({"mode": "chat", "answer": "z" * 60}),),
        )
        await db.connection.commit()
        poller = ChatEscalationIngestionPoller(db=db)
        processed = await poller.tick_once()
        assert processed == 0
