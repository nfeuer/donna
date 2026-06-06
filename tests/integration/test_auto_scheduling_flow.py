"""Integration test: creating a task via Database triggers auto-scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from donna.config import load_calendar_config
from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import Scheduler
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.database import Database
from donna.tasks.db_models import TaskDomain
from donna.tasks.events import TaskEventBus
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def cal_config():
    config_dir = Path(__file__).resolve().parents[2] / "config"
    return load_calendar_config(config_dir)


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
async def test_create_task_triggers_auto_schedule(db: Database, cal_config) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    scheduler = Scheduler(cal_config)
    auto = AutoScheduler(
        scheduler=scheduler,
        db=db,
        calendar_client=None,
        calendar_id="primary",
        notification_service=None,
    )
    bus.subscribe("task_created", auto.on_task_created)

    task = await db.create_task(
        user_id="nick",
        title="Call the mechanic",
        domain=TaskDomain.PERSONAL,
        estimated_duration=30,
        time_intent_json=TimeIntent(
            kind="exact",
            due_at=datetime.now(UTC) + timedelta(days=2),
            strictness="soft",
        ).to_json(),
    )

    # After create, the task should have been auto-scheduled
    updated = await db.get_task(task.id)
    assert updated is not None
    assert updated.status == "scheduled"
    assert updated.scheduled_start is not None
    assert updated.donna_managed is True


@pytest.mark.asyncio
async def test_challenger_pending_defers_scheduling(db: Database, cal_config) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    scheduler = Scheduler(cal_config)
    auto = AutoScheduler(
        scheduler=scheduler,
        db=db,
        calendar_client=None,
        calendar_id="primary",
        notification_service=None,
    )
    bus.subscribe("task_created", auto.on_task_created)

    task = await db.create_task(
        user_id="nick",
        title="Research new tires",
        domain=TaskDomain.PERSONAL,
        estimated_duration=30,
        challenger_pending=True,
    )

    # Task should still be in backlog — challenger is pending
    updated = await db.get_task(task.id)
    assert updated is not None
    assert updated.status == "backlog"
    assert updated.scheduled_start is None

    # Now resolve the challenger
    await auto.on_challenger_resolved(task)

    final = await db.get_task(task.id)
    assert final is not None
    assert final.status == "scheduled"
    assert final.scheduled_start is not None
