"""Tests for SeedCapabilityLoader (Task 12)."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.asyncio
async def test_loader_inserts_capability_from_yaml(tmp_path):
    from donna.skills.seed_capabilities import SeedCapabilityLoader

    yaml_file = tmp_path / "capabilities.yaml"
    yaml_file.write_text("""
capabilities:
  - name: product_watch
    description: Watch a product URL for price and availability.
    trigger_type: on_schedule
    input_schema:
      type: object
      required: [url]
      properties:
        url: {type: string}
        max_price_usd: {type: ["number", "null"]}
        required_size: {type: ["string", "null"]}
""")

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        loader = SeedCapabilityLoader(connection=conn)
        inserted = await loader.load_and_upsert(yaml_file)
        assert inserted >= 1

        cursor = await conn.execute(
            "SELECT name, trigger_type FROM capability WHERE name = 'product_watch'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "on_schedule"


@pytest.mark.asyncio
async def test_loader_is_idempotent(tmp_path):
    from donna.skills.seed_capabilities import SeedCapabilityLoader

    yaml_file = tmp_path / "capabilities.yaml"
    yaml_file.write_text("""
capabilities:
  - name: product_watch
    description: X
    trigger_type: on_schedule
    input_schema: {type: object}
""")

    db = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        loader = SeedCapabilityLoader(connection=conn)
        await loader.load_and_upsert(yaml_file)
        await loader.load_and_upsert(yaml_file)  # Idempotent — second call shouldn't duplicate.

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_loader_updates_description_on_reseed(tmp_path):
    from donna.skills.seed_capabilities import SeedCapabilityLoader

    db = tmp_path / "t3.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    yaml_file = tmp_path / "capabilities.yaml"
    yaml_file.write_text("""
capabilities:
  - name: product_watch
    description: Old description
    trigger_type: on_schedule
    input_schema: {type: object}
""")

    async with aiosqlite.connect(db) as conn:
        loader = SeedCapabilityLoader(connection=conn)
        await loader.load_and_upsert(yaml_file)

        yaml_file.write_text("""
capabilities:
  - name: product_watch
    description: New description
    trigger_type: on_schedule
    input_schema: {type: object}
""")
        await loader.load_and_upsert(yaml_file)

        cursor = await conn.execute(
            "SELECT description FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == "New description"
