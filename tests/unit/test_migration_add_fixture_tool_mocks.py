"""Test the add_fixture_tool_mocks Alembic migration."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


def _alembic_config(db_path: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.mark.asyncio
async def test_migration_adds_column_to_empty_db(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_fixture)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert "tool_mocks" in cols


@pytest.mark.asyncio
async def test_migration_backfills_captured_run_fixtures(tmp_path: Path) -> None:
    db_path = tmp_path / "populated.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "a7b8c9d0e1f2")

    async with aiosqlite.connect(db_path) as conn:
        tool_cache = {
            "cache_abc": {"tool": "web_fetch",
                          "args": {"url": "https://example.com"},
                          "result": {"status": 200, "body": "<html>OK</html>"}},
        }
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("skill_1", "cap_1", "trusted", 0),
        )
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, tool_result_cache, user_id, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("run_1", "skill_1", "ver_1", "succeeded", "{}",
             json.dumps(tool_cache), "user_nick"),
        )
        await conn.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, captured_run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("fix_1", "skill_1", "case_a", "{}", None,
             "captured_from_run", "run_1"),
        )
        await conn.commit()

    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT tool_mocks FROM skill_fixture WHERE id = ?", ("fix_1",),
        )
        row = await cursor.fetchone()
        assert row is not None
        mocks = json.loads(row[0])
        assert any("web_fetch" in key for key in mocks)


@pytest.mark.asyncio
async def test_migration_leaves_non_captured_fixtures_null(tmp_path: Path) -> None:
    db_path = tmp_path / "mixed.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "a7b8c9d0e1f2")

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("skill_2", "cap_2", "draft", 0),
        )
        await conn.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, captured_run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("fix_2", "skill_2", "case_b", "{}", None, "claude_generated", None),
        )
        await conn.commit()

    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT tool_mocks FROM skill_fixture WHERE id = ?", ("fix_2",),
        )
        row = await cursor.fetchone()
        assert row[0] is None


@pytest.mark.asyncio
async def test_migration_downgrade_drops_column(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_fixture)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert "tool_mocks" not in cols
