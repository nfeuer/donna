"""Tests for skill drafts API routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest


SCHEMA = """
    CREATE TABLE skill (
        id TEXT PRIMARY KEY,
        capability_name TEXT UNIQUE,
        current_version_id TEXT,
        state TEXT,
        requires_human_gate INTEGER,
        baseline_agreement REAL,
        created_at TEXT,
        updated_at TEXT
    );
"""


@pytest.fixture
async def db_with_skills(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(SCHEMA)
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("s1", "parse_task", "v1", "draft", 0, None, "2026-04-01T00:00:00", "2026-04-10T00:00:00"),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("s2", "summarise", "v2", "draft", 1, None, "2026-04-02T00:00:00", "2026-04-11T00:00:00"),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("s3", "extract_dates", "v3", "sandbox", 0, None, "2026-04-03T00:00:00", "2026-04-12T00:00:00"),
    )
    await conn.commit()
    yield conn
    await conn.close()


def _make_request(conn):
    request = MagicMock()
    request.app.state.db.connection = conn
    return request


async def test_list_drafts_returns_draft_state_skills(db_with_skills):
    from donna.api.routes.skill_drafts import list_skill_drafts

    request = _make_request(db_with_skills)
    result = await list_skill_drafts(request=request, limit=50)

    assert result["count"] == 2
    ids = {d["id"] for d in result["drafts"]}
    assert ids == {"s1", "s2"}
    for draft in result["drafts"]:
        assert draft["state"] == "draft"


async def test_list_drafts_excludes_other_states(db_with_skills):
    from donna.api.routes.skill_drafts import list_skill_drafts

    request = _make_request(db_with_skills)
    result = await list_skill_drafts(request=request, limit=50)

    ids = {d["id"] for d in result["drafts"]}
    assert "s3" not in ids


async def test_list_drafts_limit_parameter(db_with_skills):
    from donna.api.routes.skill_drafts import list_skill_drafts

    request = _make_request(db_with_skills)
    result = await list_skill_drafts(request=request, limit=1)

    assert result["count"] == 1
    assert len(result["drafts"]) == 1
