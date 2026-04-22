"""Smoke test that the auth migration creates the expected tables."""

from __future__ import annotations

import aiosqlite
import pytest
from alembic.config import Config as AlembicConfig

from alembic import command


@pytest.mark.asyncio
async def test_auth_migration_creates_expected_tables(tmp_path):
    db_path = tmp_path / "migration_smoke.db"
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(str(db_path)) as conn:
        rows = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {r[0] for r in rows}

    for name in (
        "trusted_ips",
        "verification_tokens",
        "ip_connections",
        "allowed_emails",
        "users",
        "device_tokens",
        "llm_gateway_callers",
    ):
        assert name in table_names, f"{name} missing from schema"
