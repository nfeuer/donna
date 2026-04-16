"""Tests for DegradationDetector — Wilson score CI degradation detection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.degradation import DegradationDetector, DegradationReport
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.lifecycle import SkillLifecycleManager
from donna.tasks.db_models import SkillState


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_CREATE_SCHEMA = """
    CREATE TABLE skill (
        id TEXT PRIMARY KEY,
        capability_name TEXT NOT NULL,
        current_version_id TEXT,
        state TEXT NOT NULL,
        requires_human_gate INTEGER NOT NULL DEFAULT 0,
        baseline_agreement REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE skill_state_transition (
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        reason TEXT NOT NULL,
        actor TEXT NOT NULL,
        actor_id TEXT,
        at TEXT NOT NULL,
        notes TEXT
    );
    CREATE TABLE skill_run (
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL
    );
    CREATE TABLE skill_divergence (
        id TEXT PRIMARY KEY,
        skill_run_id TEXT NOT NULL,
        shadow_invocation_id TEXT NOT NULL,
        overall_agreement REAL NOT NULL,
        diff_summary TEXT,
        flagged_for_evolution INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
"""


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript(_CREATE_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


async def _insert_skill(
    conn: aiosqlite.Connection,
    skill_id: str,
    state: str,
    baseline_agreement: float | None = None,
    requires_human_gate: bool = False,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO skill (id, capability_name, state, requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            skill_id,
            f"cap-{skill_id}",
            state,
            1 if requires_human_gate else 0,
            baseline_agreement,
            now,
            now,
        ),
    )
    await conn.commit()


async def _insert_divergences(
    conn: aiosqlite.Connection,
    skill_id: str,
    agreements: list[float],
) -> None:
    """Insert N divergences for a skill via a skill_run."""
    run_id = f"run-{skill_id}"
    await conn.execute(
        "INSERT OR IGNORE INTO skill_run (id, skill_id) VALUES (?, ?)",
        (run_id, skill_id),
    )
    now_base = datetime.now(timezone.utc)
    for i, agreement in enumerate(agreements):
        div_id = f"div-{skill_id}-{i}"
        ts = now_base.replace(microsecond=i).isoformat()
        await conn.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, flagged_for_evolution, created_at) "
            "VALUES (?, ?, ?, ?, NULL, 0, ?)",
            (div_id, run_id, f"shadow-{i}", agreement, ts),
        )
    await conn.commit()


def _make_config(rolling_window: int = 30, ci_confidence: float = 0.95) -> SkillSystemConfig:
    return SkillSystemConfig(
        degradation_rolling_window=rolling_window,
        degradation_ci_confidence=ci_confidence,
    )


# ---------------------------------------------------------------------------
# 1. Wilson CI — zero trials
# ---------------------------------------------------------------------------


def test_wilson_ci_zero_trials() -> None:
    lower, upper = DegradationDetector.wilson_score_ci(0, 0)
    assert lower == 0.0
    assert upper == 1.0


# ---------------------------------------------------------------------------
# 2. Wilson CI — all successes
# ---------------------------------------------------------------------------


def test_wilson_ci_all_success() -> None:
    lower, upper = DegradationDetector.wilson_score_ci(10, 10)
    # With all successes, lower should be ~0.7 and upper = 1.0
    assert lower > 0.69
    assert upper == 1.0


# ---------------------------------------------------------------------------
# 3. Wilson CI — all failures
# ---------------------------------------------------------------------------


def test_wilson_ci_all_failure() -> None:
    lower, upper = DegradationDetector.wilson_score_ci(0, 10)
    # Symmetric to all_success by complement
    assert lower == 0.0
    assert upper < 0.31


# ---------------------------------------------------------------------------
# 4. Wilson CI — half successes
# ---------------------------------------------------------------------------


def test_wilson_ci_half_success() -> None:
    lower, upper = DegradationDetector.wilson_score_ci(5, 10)
    # ~(0.24, 0.76) according to spec
    assert 0.20 < lower < 0.30
    assert 0.70 < upper < 0.80


# ---------------------------------------------------------------------------
# 5. Stable skill — no demotion
# ---------------------------------------------------------------------------


async def test_stable_skill_no_demotion(db: aiosqlite.Connection) -> None:
    """trusted skill with baseline=0.9, 30 divergences at ~0.95 → no_degradation."""
    skill_id = "stable-skill"
    await _insert_skill(db, skill_id, state="trusted", baseline_agreement=0.9)
    await _insert_divergences(db, skill_id, agreements=[0.95] * 30)

    config = _make_config(rolling_window=30)
    divergence_repo = SkillDivergenceRepository(db)
    lifecycle = SkillLifecycleManager(db)
    detector = DegradationDetector(db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    assert len(reports) == 1
    report = reports[0]
    assert report.skill_id == skill_id
    assert report.outcome == "no_degradation"

    # State should still be trusted
    cursor = await db.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row[0] == "trusted"

    # No audit row
    cursor = await db.execute("SELECT COUNT(*) FROM skill_state_transition")
    count = (await cursor.fetchone())[0]
    assert count == 0


# ---------------------------------------------------------------------------
# 6. Degraded skill — gets flagged
# ---------------------------------------------------------------------------


async def test_degraded_skill_gets_flagged(db: aiosqlite.Connection) -> None:
    """trusted skill with baseline=0.9, 30 divergences at ~0.5 → flagged_for_review."""
    skill_id = "bad-skill"
    await _insert_skill(db, skill_id, state="trusted", baseline_agreement=0.9)
    # Mix: half at 0.5 (meets threshold), half at 0.4 (below)  → avg agreement ~0.45
    # Successes: 15 out of 30 (agreement >= 0.5 counts as success)
    agreements = [0.5] * 15 + [0.4] * 15
    await _insert_divergences(db, skill_id, agreements=agreements)

    config = _make_config(rolling_window=30)
    divergence_repo = SkillDivergenceRepository(db)
    lifecycle = SkillLifecycleManager(db)
    detector = DegradationDetector(db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    assert len(reports) == 1
    report = reports[0]
    assert report.skill_id == skill_id
    assert report.outcome == "flagged"

    # State should now be flagged_for_review
    cursor = await db.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row[0] == "flagged_for_review"

    # Audit row exists
    cursor = await db.execute("SELECT COUNT(*) FROM skill_state_transition")
    count = (await cursor.fetchone())[0]
    assert count == 1


# ---------------------------------------------------------------------------
# 7. Insufficient samples — no flag
# ---------------------------------------------------------------------------


async def test_insufficient_samples_no_flag(db: aiosqlite.Connection) -> None:
    """trusted skill with baseline=0.9 but only 10 divergences → insufficient_data."""
    skill_id = "sparse-skill"
    await _insert_skill(db, skill_id, state="trusted", baseline_agreement=0.9)
    await _insert_divergences(db, skill_id, agreements=[0.4] * 10)

    config = _make_config(rolling_window=30)
    divergence_repo = SkillDivergenceRepository(db)
    lifecycle = SkillLifecycleManager(db)
    detector = DegradationDetector(db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    assert len(reports) == 1
    report = reports[0]
    assert report.skill_id == skill_id
    assert report.outcome == "insufficient_data"

    # State must not have changed
    cursor = await db.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row[0] == "trusted"

    # No audit row
    cursor = await db.execute("SELECT COUNT(*) FROM skill_state_transition")
    count = (await cursor.fetchone())[0]
    assert count == 0


# ---------------------------------------------------------------------------
# 8. Skill without baseline — skipped
# ---------------------------------------------------------------------------


async def test_skill_without_baseline_skipped(db: aiosqlite.Connection) -> None:
    """trusted skill with baseline_agreement=None → insufficient_data, no transition."""
    skill_id = "no-baseline"
    await _insert_skill(db, skill_id, state="trusted", baseline_agreement=None)
    await _insert_divergences(db, skill_id, agreements=[0.4] * 30)

    config = _make_config(rolling_window=30)
    divergence_repo = SkillDivergenceRepository(db)
    lifecycle = SkillLifecycleManager(db)
    detector = DegradationDetector(db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    assert len(reports) == 1
    report = reports[0]
    assert report.skill_id == skill_id
    assert report.outcome == "insufficient_data"

    cursor = await db.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row[0] == "trusted"


# ---------------------------------------------------------------------------
# 9. Only trusted skills are checked
# ---------------------------------------------------------------------------


async def test_only_trusted_skills_checked(db: aiosqlite.Connection) -> None:
    """shadow_primary skill with 30 bad divergences is NOT touched by DegradationDetector."""
    skill_id = "shadow-skill"
    await _insert_skill(db, skill_id, state="shadow_primary", baseline_agreement=0.9)
    await _insert_divergences(db, skill_id, agreements=[0.2] * 30)

    config = _make_config(rolling_window=30)
    divergence_repo = SkillDivergenceRepository(db)
    lifecycle = SkillLifecycleManager(db)
    detector = DegradationDetector(db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    # No trusted skills → empty report list
    assert reports == []

    # shadow_primary skill untouched
    cursor = await db.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


# ---------------------------------------------------------------------------
# 10. Flagging uses SkillLifecycleManager with correct args
# ---------------------------------------------------------------------------


async def test_flagging_uses_lifecycle_manager(db: aiosqlite.Connection) -> None:
    """On flag, transition() is called with reason='degradation', actor='system',
    and notes containing CI stats as JSON. Audit row exists in DB.
    """
    skill_id = "verify-lifecycle"
    await _insert_skill(db, skill_id, state="trusted", baseline_agreement=0.9)
    # All agreements at 0.3 — 0 successes → CI upper well below 0.9 baseline
    await _insert_divergences(db, skill_id, agreements=[0.3] * 30)

    config = _make_config(rolling_window=30)
    divergence_repo = SkillDivergenceRepository(db)

    # Wrap the real lifecycle manager so we can spy on it
    real_lifecycle = SkillLifecycleManager(db)
    original_transition = real_lifecycle.transition
    transition_calls: list[dict] = []

    async def spy_transition(skill_id, to_state, reason, actor, actor_id=None, notes=None):
        transition_calls.append(
            {
                "skill_id": skill_id,
                "to_state": to_state,
                "reason": reason,
                "actor": actor,
                "actor_id": actor_id,
                "notes": notes,
            }
        )
        return await original_transition(skill_id, to_state, reason, actor, actor_id=actor_id, notes=notes)

    real_lifecycle.transition = spy_transition  # type: ignore[method-assign]

    detector = DegradationDetector(db, divergence_repo, real_lifecycle, config)
    reports = await detector.run()

    assert len(reports) == 1
    report = reports[0]
    assert report.outcome == "flagged"

    # Verify transition() was called once with correct args
    assert len(transition_calls) == 1
    call = transition_calls[0]
    assert call["skill_id"] == skill_id
    assert call["to_state"] == SkillState.FLAGGED_FOR_REVIEW
    assert call["reason"] == "degradation"
    assert call["actor"] == "system"

    # notes should be a JSON string with CI stats
    notes = call["notes"]
    assert notes is not None
    parsed = json.loads(notes)
    assert parsed["current_successes"] == 0
    assert parsed["current_trials"] == 30
    assert "current_ci_lower" in parsed
    assert "current_ci_upper" in parsed
    assert parsed["baseline_agreement"] == 0.9

    # Audit row in DB
    cursor = await db.execute(
        "SELECT from_state, to_state, reason, actor FROM skill_state_transition WHERE skill_id = ?",
        (skill_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "trusted"
    assert row[1] == "flagged_for_review"
    assert row[2] == "degradation"
    assert row[3] == "system"
