"""Tests for SkillCandidateDetector."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.candidate_report import SkillCandidateRepository
from donna.skills.detector import SkillCandidateDetector


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE invocation_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            task_type TEXT NOT NULL,
            task_id TEXT,
            model_alias TEXT NOT NULL,
            model_actual TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            latency_ms INTEGER NOT NULL,
            tokens_in INTEGER NOT NULL,
            tokens_out INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            output TEXT,
            quality_score REAL,
            is_shadow INTEGER DEFAULT 0,
            eval_session_id TEXT,
            spot_check_queued INTEGER DEFAULT 0,
            user_id TEXT NOT NULL
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY,
            capability_name TEXT NOT NULL UNIQUE,
            current_version_id TEXT,
            state TEXT NOT NULL,
            requires_human_gate INTEGER NOT NULL DEFAULT 0,
            baseline_agreement REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
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
            reasoning TEXT,
            pattern_fingerprint TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def _insert_invocation(
    conn: aiosqlite.Connection,
    task_type: str,
    cost: float,
    output: object = None,
    days_ago: float = 0,
) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    row_id = str(uuid.uuid4())
    output_str = json.dumps(output) if output is not None else None
    await conn.execute(
        """
        INSERT INTO invocation_log
            (id, timestamp, task_type, model_alias, model_actual, input_hash,
             latency_ms, tokens_in, tokens_out, cost_usd, output, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (row_id, ts, task_type, "claude_main", "claude-sonnet-4",
         "abc123", 500, 100, 200, cost, output_str, "user_1"),
    )
    await conn.commit()


async def _insert_skill(
    conn: aiosqlite.Connection,
    capability_name: str,
    state: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    skill_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO skill
            (id, capability_name, state, requires_human_gate, created_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?)
        """,
        (skill_id, capability_name, state, now, now),
    )
    await conn.commit()


async def _insert_candidate(
    conn: aiosqlite.Connection,
    capability_name: str,
    status: str,
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    candidate_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO skill_candidate_report
            (id, capability_name, task_pattern_hash, expected_savings_usd,
             volume_30d, variance_score, status, reported_at, resolved_at)
        VALUES (?, ?, NULL, 10.0, 100, 0.5, ?, ?, NULL)
        """,
        (candidate_id, capability_name, status, now),
    )
    await conn.commit()
    return candidate_id


def _make_detector(
    conn: aiosqlite.Connection,
    min_savings: float = 5.0,
) -> SkillCandidateDetector:
    config = SkillSystemConfig(auto_draft_min_expected_savings_usd=min_savings)
    repo = SkillCandidateRepository(conn)
    return SkillCandidateDetector(
        connection=conn,
        candidate_repo=repo,
        config=config,
    )


# ---------------------------------------------------------------------------
# Test 1: creates candidate for high-savings task_type
# ---------------------------------------------------------------------------
async def test_detector_creates_candidate_for_high_savings(db):
    # 200 invocations at $0.10 each → expected_savings = 200 * 0.10 * 0.85 = $17
    for _ in range(200):
        await _insert_invocation(db, "parse_task", cost=0.10, days_ago=1)

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert len(created) == 1
    # Verify a row was actually written
    cursor = await db.execute(
        "SELECT capability_name, status FROM skill_candidate_report WHERE id = ?",
        (created[0],),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "parse_task"
    assert row[1] == "new"


# ---------------------------------------------------------------------------
# Test 2: skips task_types with expected_savings below threshold
# ---------------------------------------------------------------------------
async def test_detector_skips_low_savings(db):
    # 30 invocations at $0.15 each → expected_savings = 30 * 0.15 * 0.85 = $3.825 < $5
    for _ in range(30):
        await _insert_invocation(db, "cheap_task", cost=0.15, days_ago=1)

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []


# ---------------------------------------------------------------------------
# Test 3: skips task_types that have an active (non-claude_native) skill
# ---------------------------------------------------------------------------
async def test_detector_skips_task_types_with_active_skill(db):
    # Insert 200 invocations at $0.10, which would normally qualify
    for _ in range(200):
        await _insert_invocation(db, "has_skill_task", cost=0.10, days_ago=1)

    # But the skill table has a row with state != 'claude_native'
    await _insert_skill(db, "has_skill_task", state="sandbox")

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []


# ---------------------------------------------------------------------------
# Test 4: includes task_types with no skill row (no row means claude_native)
# ---------------------------------------------------------------------------
async def test_detector_includes_task_types_with_no_skill_row(db):
    # 200 invocations at $0.10 each — no matching skill row
    for _ in range(200):
        await _insert_invocation(db, "orphan_task", cost=0.10, days_ago=1)

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert len(created) == 1
    cursor = await db.execute(
        "SELECT capability_name FROM skill_candidate_report WHERE id = ?",
        (created[0],),
    )
    row = await cursor.fetchone()
    assert row[0] == "orphan_task"


# ---------------------------------------------------------------------------
# Test 5: idempotent — skips when existing candidate in status 'new'
# ---------------------------------------------------------------------------
async def test_detector_idempotent_skips_existing_new_candidate(db):
    for _ in range(200):
        await _insert_invocation(db, "dup_task", cost=0.10, days_ago=1)

    # Pre-existing 'new' candidate
    await _insert_candidate(db, "dup_task", status="new")

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []
    # Exactly one row still
    cursor = await db.execute(
        "SELECT COUNT(*) FROM skill_candidate_report WHERE capability_name = 'dup_task'"
    )
    (count,) = await cursor.fetchone()
    assert count == 1


# ---------------------------------------------------------------------------
# Test 6: idempotent — skips when existing candidate in status 'drafted'
# ---------------------------------------------------------------------------
async def test_detector_idempotent_skips_existing_drafted_candidate(db):
    for _ in range(200):
        await _insert_invocation(db, "drafted_task", cost=0.10, days_ago=1)

    await _insert_candidate(db, "drafted_task", status="drafted")

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []


# ---------------------------------------------------------------------------
# Test 7: creates new candidate when previous one is 'dismissed'
# ---------------------------------------------------------------------------
async def test_detector_creates_new_candidate_when_previous_dismissed(db):
    for _ in range(200):
        await _insert_invocation(db, "dismissed_task", cost=0.10, days_ago=1)

    await _insert_candidate(db, "dismissed_task", status="dismissed")

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert len(created) == 1
    cursor = await db.execute(
        "SELECT COUNT(*) FROM skill_candidate_report "
        "WHERE capability_name = 'dismissed_task' AND status = 'new'"
    )
    (count,) = await cursor.fetchone()
    assert count == 1


# ---------------------------------------------------------------------------
# Test 8: ignores invocations older than 30 days
# ---------------------------------------------------------------------------
async def test_detector_ignores_old_invocations(db):
    # 200 invocations BUT all older than 30 days
    for _ in range(200):
        await _insert_invocation(db, "old_task", cost=0.10, days_ago=31)

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []


# ---------------------------------------------------------------------------
# Test 9: variance score computation
# ---------------------------------------------------------------------------
async def test_detector_computes_variance_score(db):
    # 10 outputs with identical shape → variance = 1 - 1/10 = 0.9
    same_shape_output = {"result": "hello", "status": "ok"}
    for _ in range(10):
        await _insert_invocation(
            db, "same_shape", cost=1.0, output=same_shape_output, days_ago=1
        )

    # 10 outputs each with a unique key → 10 shapes from 10 outputs → variance = 0.0
    for i in range(10):
        await _insert_invocation(
            db, "diff_shape", cost=1.0, output={f"key_{i}": "val"}, days_ago=1
        )

    # Use a high min_savings threshold so only the 200+ tasks qualify; here
    # we lower it to ensure both 10-invocation types are processed.
    # 10 * 1.0 * 0.85 = $8.5 > $5
    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert len(created) == 2

    cursor = await db.execute(
        "SELECT capability_name, variance_score FROM skill_candidate_report "
        "ORDER BY capability_name"
    )
    rows = {r[0]: r[1] for r in await cursor.fetchall()}

    assert rows["diff_shape"] == pytest.approx(0.0)
    assert rows["same_shape"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Test 10: returns empty list when invocation_log is empty
# ---------------------------------------------------------------------------
async def test_detector_returns_empty_when_no_invocations(db):
    detector = _make_detector(db)
    created = await detector.run()
    assert created == []
