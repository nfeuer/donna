"""Smoke test that Wave 3 migrations apply and rollback cleanly.

Uses the in-process ``alembic.command`` API (matching the other migration
tests in this suite) so the target DB is an isolated tmp_path file and
doesn't collide with ``donna_tasks.db`` declared in ``alembic.ini``.
"""

from __future__ import annotations

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
async def test_wave3_migrations_apply_and_rollback(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    cfg = _cfg(db)

    # Forward: all the way to Wave 3's final head.
    command.upgrade(cfg, "a3b4c5d6e7f8")

    # Confirm new columns / indexes landed.
    async with aiosqlite.connect(db) as conn:
        cols = {row[1] async for row in await conn.execute("PRAGMA table_info(skill_candidate_report)")}
        assert "pattern_fingerprint" in cols

        idx = {row[1] async for row in await conn.execute("PRAGMA index_list(skill_candidate_report)")}
        assert "ix_skill_candidate_report_pattern_fingerprint" in idx

        auto_cols = {row[1] async for row in await conn.execute("PRAGMA table_info(automation)")}
        assert "active_cadence_cron" in auto_cols

        cap_cols = {row[1] async for row in await conn.execute("PRAGMA table_info(capability)")}
        assert "cadence_policy_override" in cap_cols

    # Rollback to the Wave 3 merge revision — both Wave 3 migrations reverse.
    command.downgrade(cfg, "e1f2a3b4c5d6")

    async with aiosqlite.connect(db) as conn:
        cols = {row[1] async for row in await conn.execute("PRAGMA table_info(skill_candidate_report)")}
        assert "pattern_fingerprint" not in cols

        auto_cols = {row[1] async for row in await conn.execute("PRAGMA table_info(automation)")}
        assert "active_cadence_cron" not in auto_cols

        cap_cols = {row[1] async for row in await conn.execute("PRAGMA table_info(capability)")}
        assert "cadence_policy_override" not in cap_cols

    # Rollback the merge itself — lands on d0e1f2a3b4c5 (one of the two
    # pre-Wave-3 heads). The other head, c2d3e4f5a6b7, is an ancestor of
    # d0e1f2a3b4c5's branch, so this is the expected resting point.
    command.downgrade(cfg, "d0e1f2a3b4c5")


@pytest.mark.asyncio
async def test_wave3_backfills_active_cadence_from_schedule(tmp_path: Path) -> None:
    """The automation migration should copy ``schedule`` into ``active_cadence_cron``."""
    db = tmp_path / "backfill.db"
    cfg = _cfg(db)

    # Upgrade up to (but not through) the automation cadence migration so we
    # can insert a pre-existing automation row with a schedule set.
    command.upgrade(cfg, "f2a3b4c5d6e7")

    async with aiosqlite.connect(db) as conn:
        # Seed the FK target.
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, created_at, created_by) "
            "VALUES ('cap_x_id', 'cap_x', 'd', '{}', 'explicit', "
            "'2026-01-01', 'test')"
        )
        await conn.execute(
            "INSERT INTO automation (id, user_id, name, capability_name, inputs, "
            "trigger_type, schedule, alert_conditions, alert_channels, "
            "min_interval_seconds, status, run_count, failure_count, "
            "created_at, updated_at, created_via) "
            "VALUES ('a1', 'u1', 'n', 'cap_x', '{}', 'schedule', '0 9 * * *', "
            "'{}', '[]', 3600, 'active', 0, 0, '2026-01-01', '2026-01-01', 'cli')"
        )
        await conn.commit()

    # Apply the cadence migration.
    command.upgrade(cfg, "a3b4c5d6e7f8")

    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT active_cadence_cron FROM automation WHERE id = 'a1'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "0 9 * * *"
