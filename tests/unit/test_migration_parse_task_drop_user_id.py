"""Test the migration that drops the bogus user_id field from parse_task.

The parse_task capability was seeded with ``user_id`` as a *required* input
field. ``user_id`` is never extractable from a user's message — it's known
from the message author — so the local extractor hallucinated it and, because
it was required, the challenger fired a spurious clarification thread. This
migration removes ``user_id`` from the parse_task input_schema entirely.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command

_PREV_HEAD = "t1r2a3c4e5i6"


def _cfg(db: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


def _parse_task_schema(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT input_schema FROM capability WHERE name = 'parse_task'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "parse_task capability row missing"
    return json.loads(row[0])


def test_user_id_removed_from_parse_task_schema(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    command.upgrade(_cfg(db), "head")
    schema = _parse_task_schema(db)
    assert "user_id" not in schema["properties"]
    assert "user_id" not in schema.get("required", [])
    # raw_text remains the real (and only required) task input.
    assert "raw_text" in schema["properties"]
    assert schema["required"] == ["raw_text"]


def test_downgrade_restores_user_id(tmp_path: Path) -> None:
    db = tmp_path / "t2.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, _PREV_HEAD)
    schema = _parse_task_schema(db)
    assert "user_id" in schema["properties"]
    assert "user_id" in schema["required"]
