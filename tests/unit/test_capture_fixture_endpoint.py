"""Tests for POST /admin/skill-runs/{id}/capture-fixture (F-W1-F)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException


async def _seed_run(conn, *, status="succeeded", final_output=None, tool_cache=None):
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, 'cap', '', '{}', 'on_message', 'active', ?, 'seed')",
        (str(uuid.uuid4()), now),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, created_at, updated_at) "
        "VALUES (?, 'cap', ?, 'trusted', 0, ?, ?)",
        (skill_id, version_id, now, now),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, created_at) "
        "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', ?)",
        (version_id, skill_id, now),
    )
    await conn.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, tool_result_cache, final_output, "
        "started_at, finished_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'nick')",
        (run_id, skill_id, version_id, status,
         json.dumps({"inputs": {"url": "https://x.com"}}),
         json.dumps(tool_cache) if tool_cache is not None else None,
         json.dumps(final_output) if final_output is not None else None,
         now, now),
    )
    await conn.commit()
    return run_id, skill_id


def _make_request(conn):
    request = MagicMock()
    request.app.state.db.connection = conn
    return request


@pytest.mark.asyncio
async def test_capture_fixture_succeeds(tmp_path):
    from donna.api.routes.skill_runs import capture_fixture

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        tool_cache = {
            "c1": {"tool": "web_fetch",
                    "args": {"url": "https://x.com"},
                    "result": {"status": 200, "body": "OK"}},
        }
        run_id, skill_id = await _seed_run(
            conn,
            final_output={"ok": True, "price_usd": 79.0, "in_stock": True},
            tool_cache=tool_cache,
        )

        request = _make_request(conn)
        result = await capture_fixture(run_id=run_id, request=request)
        assert result["source"] == "captured_from_run"
        assert "fixture_id" in result

        cursor = await conn.execute(
            "SELECT expected_output_shape, tool_mocks, captured_run_id "
            "FROM skill_fixture WHERE captured_run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        shape = json.loads(row[0])
        assert shape["type"] == "object"
        assert set(shape["required"]) == {"ok", "price_usd", "in_stock"}
        mocks = json.loads(row[1])
        assert any("web_fetch" in k for k in mocks)
        assert row[2] == run_id


@pytest.mark.asyncio
async def test_capture_fixture_404_on_missing_run(tmp_path):
    from donna.api.routes.skill_runs import capture_fixture

    db = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        request = _make_request(conn)
        with pytest.raises(HTTPException) as excinfo:
            await capture_fixture(run_id="nonexistent", request=request)
        assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_capture_fixture_409_when_run_failed(tmp_path):
    from donna.api.routes.skill_runs import capture_fixture

    db = tmp_path / "t3.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        run_id, skill_id = await _seed_run(conn, status="failed")
        request = _make_request(conn)
        with pytest.raises(HTTPException) as excinfo:
            await capture_fixture(run_id=run_id, request=request)
        assert excinfo.value.status_code == 409
