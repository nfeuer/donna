"""Integration: slice 17 Alembic revision applies cleanly forward + downgrade."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest


def _upgrade_head(db_path: Path) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def _downgrade_one(db_path: Path) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.downgrade(cfg, "-1")


@pytest.mark.integration
async def test_upgrade_creates_escalation_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "slice17_fwd.db"
    await asyncio.to_thread(_upgrade_head, db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        for table in ("escalation_request", "daily_budget_extension", "dashboard_setting"):
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            row = await cursor.fetchone()
            assert row is not None, f"{table} not created"

        cursor = await conn.execute("PRAGMA table_info(invocation_log)")
        cols = [r[1] for r in await cursor.fetchall()]
        assert "escalation_request_id" in cols


@pytest.mark.integration
async def test_downgrade_then_upgrade_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "slice17_round.db"
    await asyncio.to_thread(_upgrade_head, db_path)
    await asyncio.to_thread(_downgrade_one, db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='escalation_request'"
        )
        assert await cursor.fetchone() is None
        cursor = await conn.execute("PRAGMA table_info(invocation_log)")
        cols = [r[1] for r in await cursor.fetchall()]
        assert "escalation_request_id" not in cols

    # Re-upgrade to confirm idempotency.
    await asyncio.to_thread(_upgrade_head, db_path)
    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='escalation_request'"
        )
        assert await cursor.fetchone() is not None
