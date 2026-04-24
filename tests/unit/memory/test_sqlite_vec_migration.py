"""Alembic migration round-trip for the slice-13 schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlite_vec


def _upgrade(db_path: Path, target: str) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _downgrade(db_path: Path, target: str) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.downgrade(cfg, target)


def _tables_and_indexes(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name LIKE 'memory%' OR name LIKE 'vec_memory%' "
            "   OR name LIKE 'ix_memory%' OR name LIKE 'ux_memory%'"
        ).fetchall()
    finally:
        conn.close()
    return {name for _type, name in rows}


@pytest.mark.integration
def test_upgrade_creates_memory_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "mem.db"
    _upgrade(db_path, "head")
    names = _tables_and_indexes(db_path)
    for expected in (
        "memory_documents",
        "memory_chunks",
        "vec_memory_chunks",
        "ix_memory_doc_user_updated",
        "ix_memory_doc_user_deleted",
        "ix_memory_chunk_doc",
        "ix_memory_chunk_user_version",
    ):
        assert expected in names, f"missing {expected!r} (got {sorted(names)})"


@pytest.mark.integration
def test_downgrade_reverses_cleanly(tmp_path: Path) -> None:
    db_path = tmp_path / "mem.db"
    _upgrade(db_path, "head")
    _downgrade(db_path, "-1")
    names = _tables_and_indexes(db_path)
    # All slice-13 objects should be gone.
    assert not any(n.startswith(("memory_", "vec_memory_")) for n in names)


@pytest.mark.integration
def test_upgrade_after_downgrade_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "mem.db"
    _upgrade(db_path, "head")
    _downgrade(db_path, "-1")
    _upgrade(db_path, "head")
    names = _tables_and_indexes(db_path)
    assert "memory_documents" in names
    assert "vec_memory_chunks" in names
