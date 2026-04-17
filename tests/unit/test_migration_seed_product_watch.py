"""Tests for seed_product_watch_capability migration."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


def _cfg(db: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.mark.asyncio
async def test_seed_creates_capability(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_seed_creates_skill_in_sandbox_state(tmp_path: Path) -> None:
    db = tmp_path / "t2.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT state FROM skill WHERE capability_name = 'product_watch'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "sandbox"


@pytest.mark.asyncio
async def test_seed_creates_skill_version(tmp_path: Path) -> None:
    db = tmp_path / "t3.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM skill_version sv "
            "JOIN skill s ON sv.skill_id = s.id "
            "WHERE s.capability_name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_seed_creates_four_fixtures_with_tool_mocks(tmp_path: Path) -> None:
    db = tmp_path / "t4.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT case_name, tool_mocks FROM skill_fixture sf "
            "JOIN skill s ON sf.skill_id = s.id "
            "WHERE s.capability_name = 'product_watch'"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 4
        for case_name, mocks_json in rows:
            mocks = json.loads(mocks_json)
            assert any("web_fetch" in k for k in mocks), f"{case_name}: no web_fetch in mocks"


@pytest.mark.asyncio
async def test_seed_downgrade_removes_capability(tmp_path: Path) -> None:
    db = tmp_path / "t5.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 0
