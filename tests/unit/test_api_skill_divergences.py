"""Tests for skill run divergence API routes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
from fastapi import HTTPException

SCHEMA = """
    CREATE TABLE skill_run (
        id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
        task_id TEXT, automation_run_id TEXT, status TEXT,
        total_latency_ms INTEGER, total_cost_usd REAL,
        state_object TEXT, tool_result_cache TEXT, final_output TEXT,
        escalation_reason TEXT, error TEXT, user_id TEXT,
        started_at TEXT, finished_at TEXT
    );
    CREATE TABLE skill_divergence (
        id TEXT PRIMARY KEY,
        skill_run_id TEXT NOT NULL,
        shadow_invocation_id TEXT,
        overall_agreement REAL NOT NULL,
        diff_summary TEXT,
        flagged_for_evolution INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
"""


@pytest.fixture
async def db_with_divergence(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(SCHEMA)
    await conn.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "s1", "v1", "succeeded", "{}", "nick", "2026-04-15T10:00:00"),
    )
    diff_summary = json.dumps({"fields_differing": ["output_text"], "severity": "minor"})
    await conn.execute(
        "INSERT INTO skill_divergence "
        "(id, skill_run_id, shadow_invocation_id, overall_agreement, "
        "diff_summary, flagged_for_evolution, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("d1", "r1", "inv1", 0.85, diff_summary, 0, "2026-04-15T10:00:05"),
    )
    await conn.commit()
    yield conn
    await conn.close()


def _make_request(conn):
    request = MagicMock()
    request.app.state.db.connection = conn
    return request


async def test_get_divergence_returns_row_when_exists(db_with_divergence):
    from donna.api.routes.skill_runs import get_skill_run_divergence

    request = _make_request(db_with_divergence)
    result = await get_skill_run_divergence(skill_run_id="r1", request=request)

    assert result["id"] == "d1"
    assert result["skill_run_id"] == "r1"
    assert result["shadow_invocation_id"] == "inv1"
    assert abs(result["overall_agreement"] - 0.85) < 1e-9
    assert result["flagged_for_evolution"] is False
    assert isinstance(result["diff_summary"], dict)
    assert result["diff_summary"]["severity"] == "minor"


async def test_get_divergence_404_when_none(db_with_divergence):
    from donna.api.routes.skill_runs import get_skill_run_divergence

    request = _make_request(db_with_divergence)
    with pytest.raises(HTTPException) as excinfo:
        await get_skill_run_divergence(skill_run_id="missing", request=request)
    assert excinfo.value.status_code == 404
