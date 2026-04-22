"""Test the add_manual_draft_at Alembic migration."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from alembic.config import Config

from alembic import command


def _cfg(db: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.mark.asyncio
async def test_column_added(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_candidate_report)")
        cols = {r[1] for r in await cursor.fetchall()}
        assert "manual_draft_at" in cols


@pytest.mark.asyncio
async def test_index_added(tmp_path: Path) -> None:
    db = tmp_path / "t2.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("PRAGMA index_list(skill_candidate_report)")
        indexes = {r[1] for r in await cursor.fetchall()}
        assert "ix_skill_candidate_report_manual_draft_at" in indexes


@pytest.mark.asyncio
async def test_downgrade_drops_column(tmp_path: Path) -> None:
    db = tmp_path / "t3.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    # Downgrade past this migration specifically (target = its down_revision).
    command.downgrade(cfg, "b8c9d0e1f2a3")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_candidate_report)")
        cols = {r[1] for r in await cursor.fetchall()}
        assert "manual_draft_at" not in cols
