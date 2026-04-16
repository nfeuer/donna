from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from donna.automations.repository import AutomationRepository


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT, input_schema TEXT, trigger_type TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, embedding BLOB
        );
        CREATE TABLE automation (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            name TEXT NOT NULL, description TEXT,
            capability_name TEXT NOT NULL,
            inputs TEXT NOT NULL, trigger_type TEXT NOT NULL,
            schedule TEXT, alert_conditions TEXT NOT NULL,
            alert_channels TEXT NOT NULL,
            max_cost_per_run_usd REAL, min_interval_seconds INTEGER NOT NULL,
            status TEXT NOT NULL, last_run_at TEXT, next_run_at TEXT,
            run_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_via TEXT NOT NULL
        );
        CREATE TABLE automation_run (
            id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, execution_path TEXT NOT NULL,
            skill_run_id TEXT, invocation_log_id TEXT,
            output TEXT, alert_sent INTEGER NOT NULL DEFAULT 0,
            alert_content TEXT, error TEXT, cost_usd REAL
        );
    """)
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'product_watch', 'cap', '{}', 'on_schedule', 'active', ?, 'seed')",
        (now,),
    )
    await conn.commit()
    yield conn
    await conn.close()


async def _create(repo, *, name="Test Auto", status_override=None, next_run_at=None):
    auto_id = await repo.create(
        user_id="nick", name=name, description=None,
        capability_name="product_watch",
        inputs={"url": "https://example.com"},
        trigger_type="on_schedule", schedule="0 12 * * *",
        alert_conditions={"all_of": []},
        alert_channels=["discord"],
        max_cost_per_run_usd=2.0,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=next_run_at,
    )
    if status_override is not None:
        await repo.set_status(auto_id, status_override)
    return auto_id


async def test_create_and_get(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Watch shirt")
    row = await repo.get(auto_id)
    assert row is not None
    assert row.name == "Watch shirt"
    assert row.inputs == {"url": "https://example.com"}
    assert row.status == "active"
    assert row.run_count == 0


async def test_get_returns_none_for_missing(db):
    repo = AutomationRepository(db)
    assert await repo.get("missing") is None


async def test_list_all_filters_by_status(db):
    repo = AutomationRepository(db)
    await _create(repo, name="A", status_override="active")
    await _create(repo, name="B", status_override="paused")
    await _create(repo, name="C", status_override="active")

    actives = await repo.list_all(status="active")
    assert {r.name for r in actives} == {"A", "C"}
    paused = await repo.list_all(status="paused")
    assert {r.name for r in paused} == {"B"}


async def test_list_due_returns_rows_with_next_run_before_now_and_active(db):
    repo = AutomationRepository(db)
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=5)
    future = now + timedelta(minutes=5)
    await _create(repo, name="Due", next_run_at=past)
    await _create(repo, name="Not yet", next_run_at=future)
    await _create(repo, name="Paused", next_run_at=past, status_override="paused")
    await _create(repo, name="No next", next_run_at=None)

    due = await repo.list_due(now)
    due_names = {r.name for r in due}
    assert "Due" in due_names
    assert "Not yet" not in due_names
    assert "Paused" not in due_names
    assert "No next" not in due_names


async def test_advance_schedule_updates_counters_and_times(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    now = datetime.now(timezone.utc)
    later = now + timedelta(days=1)

    await repo.advance_schedule(
        automation_id=auto_id, last_run_at=now,
        next_run_at=later, increment_run_count=True,
        increment_failure_count=False,
    )
    row = await repo.get(auto_id)
    assert row.run_count == 1
    assert row.failure_count == 0
    assert row.last_run_at.replace(microsecond=0) == now.replace(microsecond=0)


async def test_advance_schedule_increments_failure_counter(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    now = datetime.now(timezone.utc)
    await repo.advance_schedule(
        automation_id=auto_id, last_run_at=now, next_run_at=None,
        increment_run_count=True, increment_failure_count=True,
    )
    row = await repo.get(auto_id)
    assert row.run_count == 1
    assert row.failure_count == 1


async def test_reset_failure_count(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    now = datetime.now(timezone.utc)
    await repo.advance_schedule(
        automation_id=auto_id, last_run_at=now, next_run_at=None,
        increment_run_count=True, increment_failure_count=True,
    )
    await repo.reset_failure_count(auto_id)
    row = await repo.get(auto_id)
    assert row.failure_count == 0


async def test_insert_and_finish_run(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    started = datetime.now(timezone.utc)

    run_id = await repo.insert_run(
        automation_id=auto_id, started_at=started,
        execution_path="claude_native",
    )
    await repo.finish_run(
        run_id=run_id, status="succeeded",
        output={"price": 42}, skill_run_id=None,
        invocation_log_id="inv-1", alert_sent=True,
        alert_content="price dropped", error=None, cost_usd=0.05,
    )
    runs = await repo.list_runs(auto_id)
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].alert_sent is True
    assert runs[0].cost_usd == 0.05


async def test_list_runs_ordered_newest_first(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    for i in range(3):
        started = datetime.now(timezone.utc) + timedelta(seconds=i)
        run_id = await repo.insert_run(
            automation_id=auto_id, started_at=started,
            execution_path="claude_native",
        )
        await repo.finish_run(
            run_id=run_id, status="succeeded",
            output={"n": i}, skill_run_id=None, invocation_log_id=None,
            alert_sent=False, alert_content=None, error=None, cost_usd=0.0,
        )
    runs = await repo.list_runs(auto_id, limit=10)
    assert len(runs) == 3
    assert [r.output["n"] for r in runs] == [2, 1, 0]
