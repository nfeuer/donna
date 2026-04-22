from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
from fastapi import HTTPException


@pytest.fixture
async def db_with_runs(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
            task_id TEXT, automation_run_id TEXT, status TEXT,
            total_latency_ms INTEGER, total_cost_usd REAL,
            state_object TEXT, tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT,
            started_at TEXT, finished_at TEXT
        );
        CREATE INDEX ix_skill_run_skill_id ON skill_run(skill_id);
        CREATE INDEX ix_skill_run_started_at ON skill_run(started_at);
        CREATE TABLE skill_step_result (
            id TEXT PRIMARY KEY, skill_run_id TEXT, step_name TEXT,
            step_index INTEGER, step_kind TEXT, invocation_log_id TEXT,
            prompt_tokens INTEGER, output TEXT, tool_calls TEXT,
            latency_ms INTEGER, validation_status TEXT, error TEXT,
            created_at TEXT
        );
    """)
    await conn.execute(
        "INSERT INTO skill_run "
        "(id, skill_id, skill_version_id, status, state_object, "
        "user_id, started_at, total_latency_ms) "
        "VALUES ('r1', 's1', 'v1', 'succeeded', '{}', 'nick', "
        "'2026-04-15T10:00:00', 150)"
    )
    await conn.execute(
        "INSERT INTO skill_run "
        "(id, skill_id, skill_version_id, status, state_object, "
        "user_id, started_at, total_latency_ms) "
        "VALUES ('r2', 's1', 'v1', 'failed', '{}', 'nick', "
        "'2026-04-15T11:00:00', 75)"
    )
    await conn.execute(
        "INSERT INTO skill_step_result "
        "(id, skill_run_id, step_name, step_index, step_kind, output, "
        "latency_ms, validation_status, created_at) "
        "VALUES ('sr1', 'r1', 'extract', 0, 'llm', '{\"title\":\"x\"}', "
        "50, 'valid', '2026-04-15T10:00:01')"
    )
    await conn.commit()
    yield conn
    await conn.close()


async def test_list_runs_for_skill(db_with_runs):
    from donna.api.routes.skill_runs import list_runs_for_skill

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    result = await list_runs_for_skill(skill_id="s1", request=request, limit=100, offset=0)

    assert result["count"] == 2
    assert {r["id"] for r in result["runs"]} == {"r1", "r2"}


async def test_get_run_detail(db_with_runs):
    from donna.api.routes.skill_runs import get_run_detail

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    result = await get_run_detail(run_id="r1", request=request)

    assert result["id"] == "r1"
    assert len(result["step_results"]) == 1
    assert result["step_results"][0]["step_name"] == "extract"


async def test_get_run_detail_404(db_with_runs):
    from donna.api.routes.skill_runs import get_run_detail

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    with pytest.raises(HTTPException) as excinfo:
        await get_run_detail(run_id="missing", request=request)
    assert excinfo.value.status_code == 404


async def test_list_recent_runs(db_with_runs):
    from donna.api.routes.skill_runs import list_recent_runs

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    result = await list_recent_runs(request=request, status=None, limit=100)

    assert result["count"] == 2


async def test_list_recent_runs_with_status_filter(db_with_runs):
    from donna.api.routes.skill_runs import list_recent_runs

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    result = await list_recent_runs(request=request, status="failed", limit=100)

    assert result["count"] == 1
    assert result["runs"][0]["id"] == "r2"
