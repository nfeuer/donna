"""Tests that Database emits events via TaskEventBus."""

from __future__ import annotations

from pathlib import Path

import pytest

from donna.tasks.database import Database
from donna.tasks.db_models import TaskDomain, TaskStatus
from donna.tasks.events import TaskEventBus
from donna.tasks.state_machine import StateMachine


@pytest.fixture
async def db(tmp_path: Path, state_machine: StateMachine) -> Database:
    db_path = tmp_path / "test.db"
    database = Database(db_path, state_machine)
    await database.connect()
    conn = database.connection
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            domain TEXT DEFAULT 'personal',
            priority INTEGER DEFAULT 2,
            status TEXT DEFAULT 'backlog',
            estimated_duration INTEGER,
            deadline TEXT,
            deadline_type TEXT DEFAULT 'none',
            scheduled_start TEXT,
            actual_start TEXT,
            completed_at TEXT,
            recurrence TEXT,
            dependencies TEXT,
            parent_task TEXT,
            prep_work_flag INTEGER DEFAULT 0,
            prep_work_instructions TEXT,
            agent_eligible INTEGER DEFAULT 0,
            assigned_agent TEXT,
            agent_status TEXT,
            tags TEXT,
            notes TEXT,
            reschedule_count INTEGER DEFAULT 0,
            created_at TEXT,
            created_via TEXT DEFAULT 'discord',
            estimated_cost REAL,
            calendar_event_id TEXT,
            donna_managed INTEGER DEFAULT 0,
            nudge_count INTEGER DEFAULT 0,
            quality_score REAL,
            capability_name TEXT,
            inputs_json TEXT,
            time_intent_json TEXT
        )
    """)
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_task_emits_event(db: Database) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    received: list[dict] = []

    async def handler(task, **ctx):
        received.append({"task": task, **ctx})

    bus.subscribe("task_created", handler)

    task = await db.create_task(
        user_id="nick",
        title="Call the mechanic",
        domain=TaskDomain.PERSONAL,
    )

    assert len(received) == 1
    assert received[0]["task"].id == task.id
    assert received[0]["task"].title == "Call the mechanic"


@pytest.mark.asyncio
async def test_create_task_no_bus(db: Database) -> None:
    task = await db.create_task(
        user_id="nick",
        title="No bus task",
    )
    assert task.title == "No bus task"


@pytest.mark.asyncio
async def test_transition_emits_state_changed(db: Database) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    received: list[dict] = []

    async def handler(task, **ctx):
        received.append({"task": task, **ctx})

    bus.subscribe("task_state_changed", handler)

    task = await db.create_task(
        user_id="nick",
        title="Schedule me",
    )
    await db.transition_task_state(task.id, TaskStatus.SCHEDULED)

    assert len(received) == 1
    assert received[0]["old_status"] == "backlog"
    assert received[0]["new_status"] == "scheduled"
    assert received[0]["task"].id == task.id
