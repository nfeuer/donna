"""SkillCandidateDetector skips patterns already registered as claude_native.

Wave 3 added a ``claude_native_registered`` status to
``skill_candidate_report``. The detector must treat rows with that status
as a permanent veto — once Claude has said "this pattern is one-off /
user-specific / low-value", the detector should stop proposing the same
task_type as a new candidate until the row is manually flipped.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.candidate_report import (
    SkillCandidateRepository,
    fingerprint_message,
)
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
            user_id TEXT NOT NULL,
            skill_id TEXT
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


async def _seed_invocation(
    conn: aiosqlite.Connection,
    task_type: str,
    cost: float,
    days_ago: float = 1,
) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    await conn.execute(
        """
        INSERT INTO invocation_log
            (id, timestamp, task_type, model_alias, model_actual, input_hash,
             latency_ms, tokens_in, tokens_out, cost_usd, output, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), ts, task_type, "claude_main", "claude-sonnet-4",
         "abc123", 500, 100, 200, cost, json.dumps({"ok": True}), "user_1"),
    )
    await conn.commit()


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


@pytest.mark.asyncio
async def test_detector_skips_claude_native_registered_capability(db):
    """A capability flagged claude_native_registered is NOT re-surfaced."""
    # A task_type that would otherwise be proposed:
    for _ in range(200):
        await _seed_invocation(db, "novel_pattern", cost=0.10)

    # But a prior row has already been flagged claude_native_registered for it.
    now = datetime.now(UTC).isoformat()
    await db.execute(
        """
        INSERT INTO skill_candidate_report
            (id, capability_name, task_pattern_hash, expected_savings_usd,
             volume_30d, variance_score, status, reported_at, resolved_at,
             pattern_fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rpt-1", "novel_pattern", "claude-said-one-off", 0.0, 0, 0.0,
         "claude_native_registered", now, now, "fp-hash-abc"),
    )
    await db.commit()

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []
    # Exactly one row still exists — no duplicate was created.
    cursor = await db.execute(
        "SELECT COUNT(*) FROM skill_candidate_report WHERE capability_name = 'novel_pattern'"
    )
    (count,) = await cursor.fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_detector_still_runs_for_unrelated_capabilities(db):
    """claude_native_registered should not globally halt detection."""
    for _ in range(200):
        await _seed_invocation(db, "blocked", cost=0.10)
    for _ in range(200):
        await _seed_invocation(db, "allowed", cost=0.10)

    now = datetime.now(UTC).isoformat()
    await db.execute(
        """
        INSERT INTO skill_candidate_report
            (id, capability_name, task_pattern_hash, expected_savings_usd,
             volume_30d, variance_score, status, reported_at, resolved_at,
             pattern_fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("rpt-blocked", "blocked", None, 0.0, 0, 0.0,
         "claude_native_registered", now, now, "fp-blocked"),
    )
    await db.commit()

    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert len(created) == 1
    cursor = await db.execute(
        "SELECT capability_name FROM skill_candidate_report WHERE id = ?",
        (created[0],),
    )
    row = await cursor.fetchone()
    assert row[0] == "allowed"


@pytest.mark.asyncio
async def test_repo_upsert_claude_native_registered_is_idempotent(db):
    """Calling upsert twice for the same fingerprint does not duplicate."""
    repo = SkillCandidateRepository(db)

    id1 = await repo.upsert_claude_native_registered(
        fingerprint="fp-xyz",
        reasoning="Tax prep — user-specific, low frequency",
    )
    id2 = await repo.upsert_claude_native_registered(
        fingerprint="fp-xyz",
        reasoning="Tax prep — updated reasoning",
    )

    assert id1 == id2
    cursor = await db.execute(
        "SELECT COUNT(*) FROM skill_candidate_report "
        "WHERE pattern_fingerprint = 'fp-xyz' AND status = 'claude_native_registered'"
    )
    (count,) = await cursor.fetchone()
    assert count == 1

    # Second call's reasoning persisted.
    cursor = await db.execute(
        "SELECT reasoning FROM skill_candidate_report WHERE id = ?",
        (id1,),
    )
    row = await cursor.fetchone()
    assert row[0] == "Tax prep — updated reasoning"


@pytest.mark.asyncio
async def test_detector_skips_when_fingerprint_matches_registered_row(db):
    """End-to-end: dispatcher writes row via upsert_claude_native_registered
    keyed on fingerprint_message(task_type); detector consults
    list_claude_native_registered_fingerprints() and skips the matching row
    even though capability_name is NULL (which the capability-name skip
    cannot catch).
    """
    task_type = "novel_pattern"

    # 1. Seed enough invocations for the detector to propose this task_type.
    for _ in range(200):
        await _seed_invocation(db, task_type, cost=0.10)

    # 2. Dispatcher writes a claude_native_registered row whose
    #    pattern_fingerprint is fingerprint_message(task_type). This simulates
    #    the case where a prior utterance normalised to the same string as
    #    the detector's task_type (rare, but the guard targets exactly this).
    repo = SkillCandidateRepository(db)
    dispatcher_written_id = await repo.upsert_claude_native_registered(
        fingerprint=fingerprint_message(task_type),
        reasoning="Claude decided this is one-off / user-specific.",
    )

    # Sanity: capability_name IS NULL on the row, so the capability-name
    # skip would NOT catch it on its own.
    cursor = await db.execute(
        "SELECT capability_name FROM skill_candidate_report WHERE id = ?",
        (dispatcher_written_id,),
    )
    (cap_name,) = await cursor.fetchone()
    assert cap_name is None

    # 3. Detector runs — must skip the task_type via the fingerprint guard.
    detector = _make_detector(db, min_savings=5.0)
    created = await detector.run()

    assert created == []

    # Only the dispatcher's row exists for this fingerprint; no new 'new' rows.
    cursor = await db.execute(
        "SELECT COUNT(*) FROM skill_candidate_report WHERE status = 'new'"
    )
    (new_count,) = await cursor.fetchone()
    assert new_count == 0
