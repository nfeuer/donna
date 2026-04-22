import json
from pathlib import Path

import aiosqlite
import pytest

from donna.skills.run_persistence import SkillRunRepository


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
            task_id TEXT, automation_run_id TEXT, status TEXT NOT NULL,
            total_latency_ms INTEGER, total_cost_usd REAL,
            state_object TEXT NOT NULL, tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE skill_step_result (
            id TEXT PRIMARY KEY, skill_run_id TEXT NOT NULL,
            step_name TEXT NOT NULL, step_index INTEGER NOT NULL,
            step_kind TEXT NOT NULL, invocation_log_id TEXT,
            prompt_tokens INTEGER, output TEXT, tool_calls TEXT,
            latency_ms INTEGER, validation_status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_start_run_creates_row(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={"raw_text": "hi"}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    cursor = await db.execute("SELECT status FROM skill_run WHERE id = ?", (run_id,))
    row = await cursor.fetchone()
    assert row[0] == "running"


async def test_record_step_creates_row(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    await repo.record_step(
        skill_run_id=run_id,
        step_name="extract", step_index=0, step_kind="llm",
        output={"title": "x"}, latency_ms=50,
        validation_status="valid", invocation_log_id="inv-1",
        tool_calls=None, error=None,
    )

    cursor = await db.execute(
        "SELECT step_name, output FROM skill_step_result WHERE skill_run_id = ?",
        (run_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "extract"
    assert json.loads(rows[0][1]) == {"title": "x"}


async def test_finish_run_updates_row(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    await repo.finish_run(
        skill_run_id=run_id,
        status="succeeded",
        final_output={"priority": 3},
        state_object={"extract": {"title": "x"}, "classify": {"priority": 3}},
        tool_result_cache={},
        total_latency_ms=100,
        total_cost_usd=0.0,
        escalation_reason=None, error=None,
    )

    cursor = await db.execute(
        "SELECT status, final_output, total_latency_ms FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "succeeded"
    assert json.loads(row[1]) == {"priority": 3}
    assert row[2] == 100


async def test_multiple_steps_ordered_by_index(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    for i, name in enumerate(["a", "b", "c"]):
        await repo.record_step(
            skill_run_id=run_id,
            step_name=name, step_index=i, step_kind="llm",
            output={"v": i}, latency_ms=10, validation_status="valid",
        )

    cursor = await db.execute(
        "SELECT step_name FROM skill_step_result WHERE skill_run_id = ? ORDER BY step_index",
        (run_id,),
    )
    names = [r[0] for r in await cursor.fetchall()]
    assert names == ["a", "b", "c"]
