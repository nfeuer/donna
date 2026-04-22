"""Tests for skill candidate API routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
from fastapi import HTTPException

SCHEMA = """
    CREATE TABLE skill_candidate_report (
        id TEXT PRIMARY KEY,
        capability_name TEXT,
        task_pattern_hash TEXT,
        expected_savings_usd REAL NOT NULL,
        volume_30d INTEGER NOT NULL,
        variance_score REAL,
        status TEXT NOT NULL,
        reported_at TEXT NOT NULL,
        resolved_at TEXT,
        manual_draft_at TEXT,
        reasoning TEXT
    );
"""


@pytest.fixture
async def db_with_candidates(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(SCHEMA)
    # Insert test candidates
    await conn.execute(
        "INSERT INTO skill_candidate_report "
        "(id, capability_name, task_pattern_hash, expected_savings_usd, "
        "volume_30d, variance_score, status, reported_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c1", "parse_task", "hash1", 20.0, 300, 0.1, "new", "2026-04-01T00:00:00", None),
    )
    await conn.execute(
        "INSERT INTO skill_candidate_report "
        "(id, capability_name, task_pattern_hash, expected_savings_usd, "
        "volume_30d, variance_score, status, reported_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c2", "summarise", "hash2", 5.0, 100, 0.3, "new", "2026-04-02T00:00:00", None),
    )
    await conn.execute(
        "INSERT INTO skill_candidate_report "
        "(id, capability_name, task_pattern_hash, expected_savings_usd, "
        "volume_30d, variance_score, status, reported_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c3", "extract_dates", "hash3", 12.0, 150, 0.2, "dismissed", "2026-03-30T00:00:00", "2026-04-01T00:00:00"),
    )
    await conn.commit()
    yield conn
    await conn.close()


def _make_request(conn):
    request = MagicMock()
    request.app.state.db.connection = conn
    return request


async def test_list_candidates_returns_new_candidates(db_with_candidates):
    from donna.api.routes.skill_candidates import list_skill_candidates

    request = _make_request(db_with_candidates)
    result = await list_skill_candidates(request=request, status="new", limit=50)

    assert result["count"] == 2
    ids = {c["id"] for c in result["candidates"]}
    assert ids == {"c1", "c2"}


async def test_list_candidates_filters_by_status(db_with_candidates):
    from donna.api.routes.skill_candidates import list_skill_candidates

    request = _make_request(db_with_candidates)
    result = await list_skill_candidates(request=request, status="dismissed", limit=50)

    assert result["count"] == 1
    assert result["candidates"][0]["id"] == "c3"


async def test_list_candidates_orders_by_savings_desc(db_with_candidates):
    from donna.api.routes.skill_candidates import list_skill_candidates

    request = _make_request(db_with_candidates)
    result = await list_skill_candidates(request=request, status="new", limit=50)

    savings = [c["expected_savings_usd"] for c in result["candidates"]]
    assert savings == sorted(savings, reverse=True)
    assert result["candidates"][0]["id"] == "c1"


async def test_dismiss_candidate_changes_status_to_dismissed(db_with_candidates):
    from donna.api.routes.skill_candidates import dismiss_candidate

    request = _make_request(db_with_candidates)
    result = await dismiss_candidate(candidate_id="c1", request=request)

    assert result["candidate_id"] == "c1"
    assert result["status"] == "dismissed"

    # Verify in DB
    cursor = await db_with_candidates.execute(
        "SELECT status FROM skill_candidate_report WHERE id = 'c1'"
    )
    row = await cursor.fetchone()
    assert row[0] == "dismissed"


async def test_dismiss_candidate_404(db_with_candidates):
    from donna.api.routes.skill_candidates import dismiss_candidate

    request = _make_request(db_with_candidates)
    with pytest.raises(HTTPException) as excinfo:
        await dismiss_candidate(candidate_id="missing", request=request)
    assert excinfo.value.status_code == 404


async def test_draft_now_202_sets_manual_draft_at(db_with_candidates):
    """After Wave 2 F-W1-D, draft-now returns 202 and sets manual_draft_at.
    The orchestrator's ManualDraftPoller picks it up async.
    """
    from donna.api.routes.skill_candidates import draft_candidate_now

    request = _make_request(db_with_candidates)
    # Do NOT set auto_drafter on app.state — not required anymore post-Wave-2.
    if hasattr(request.app.state, "auto_drafter"):
        del request.app.state.auto_drafter

    result = await draft_candidate_now(candidate_id="c1", request=request)
    assert result["status"] == "scheduled"
    assert "manual_draft_at" in result

    cursor = await db_with_candidates.execute(
        "SELECT manual_draft_at FROM skill_candidate_report WHERE id = 'c1'"
    )
    row = await cursor.fetchone()
    assert row[0] is not None


async def test_draft_now_404_for_missing_candidate(db_with_candidates):
    from donna.api.routes.skill_candidates import draft_candidate_now

    request = _make_request(db_with_candidates)

    with pytest.raises(HTTPException) as excinfo:
        await draft_candidate_now(candidate_id="nonexistent", request=request)
    assert excinfo.value.status_code == 404


async def test_draft_now_404_for_non_new_candidate(db_with_candidates):
    from donna.api.routes.skill_candidates import draft_candidate_now

    request = _make_request(db_with_candidates)
    # c3 is 'dismissed', not 'new'
    with pytest.raises(HTTPException) as excinfo:
        await draft_candidate_now(candidate_id="c3", request=request)
    assert excinfo.value.status_code == 404
