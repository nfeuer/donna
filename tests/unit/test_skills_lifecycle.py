"""Tests for SkillLifecycleManager — sole mutator of skill.state."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.lifecycle import (
    HumanGateRequiredError,
    IllegalTransitionError,
    SkillLifecycleManager,
    SkillNotFoundError,
)
from donna.tasks.db_models import SkillState


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
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
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def _insert_skill(
    db: aiosqlite.Connection,
    skill_id: str = "s1",
    state: str = "sandbox",
    requires_human_gate: bool = False,
) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, state, requires_human_gate, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (skill_id, f"cap-{skill_id}", state, 1 if requires_human_gate else 0, now, now),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Happy-path tests — one for every legal transition
# ---------------------------------------------------------------------------


async def test_claude_native_to_skill_candidate(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="claude_native")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.SKILL_CANDIDATE, reason="gate_passed", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "skill_candidate"


async def test_skill_candidate_to_draft(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="skill_candidate")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.DRAFT, reason="gate_passed", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "draft"


async def test_draft_to_sandbox(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="draft")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.SANDBOX, reason="human_approval", actor="user")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"


async def test_sandbox_to_shadow_primary(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="sandbox")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.SHADOW_PRIMARY, reason="gate_passed", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


async def test_shadow_primary_to_trusted(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="shadow_primary")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.TRUSTED, reason="gate_passed", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "trusted"


async def test_trusted_to_flagged_for_review(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="trusted")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.FLAGGED_FOR_REVIEW, reason="degradation", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "flagged_for_review"


async def test_shadow_primary_to_flagged_for_review_is_legal(db_with_runs: aiosqlite.Connection) -> None:
    await _insert_skill(db_with_runs, skill_id="s1", state="shadow_primary")
    manager = SkillLifecycleManager(db_with_runs, SkillSystemConfig())
    await manager.transition(
        skill_id="s1", to_state=SkillState.FLAGGED_FOR_REVIEW,
        reason="degradation", actor="system",
    )
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "flagged_for_review"


async def test_flagged_for_review_to_trusted(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="flagged_for_review")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.TRUSTED, reason="human_approval", actor="user")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "trusted"


async def test_flagged_for_review_to_degraded(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="flagged_for_review")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.DEGRADED, reason="human_approval", actor="user")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "degraded"


async def test_degraded_to_draft(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="degraded")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.DRAFT, reason="gate_passed", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "draft"


async def test_degraded_to_claude_native(db: aiosqlite.Connection) -> None:
    await _insert_skill(db, state="degraded")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.CLAUDE_NATIVE, reason="evolution_failed", actor="system")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "claude_native"


async def test_any_to_claude_native_manual_override(db: aiosqlite.Connection) -> None:
    """trusted → claude_native with manual_override is always allowed."""
    await _insert_skill(db, state="trusted")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.CLAUDE_NATIVE, reason="manual_override", actor="user")
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "claude_native"


# ---------------------------------------------------------------------------
# Illegal transition tests
# ---------------------------------------------------------------------------


async def test_illegal_draft_to_trusted(db: aiosqlite.Connection) -> None:
    """draft → trusted skips sandbox and shadow_primary — not in table."""
    await _insert_skill(db, state="draft")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(IllegalTransitionError):
        await mgr.transition("s1", SkillState.TRUSTED, reason="gate_passed", actor="system")


async def test_illegal_self_loop_sandbox(db: aiosqlite.Connection) -> None:
    """sandbox → sandbox is always illegal."""
    await _insert_skill(db, state="sandbox")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(IllegalTransitionError, match="Self-loop"):
        await mgr.transition("s1", SkillState.SANDBOX, reason="gate_passed", actor="system")


async def test_illegal_wrong_reason_flagged_to_claude_native(db: aiosqlite.Connection) -> None:
    """flagged_for_review → claude_native with reason=gate_passed: wrong reason."""
    await _insert_skill(db, state="flagged_for_review")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(IllegalTransitionError):
        await mgr.transition("s1", SkillState.CLAUDE_NATIVE, reason="gate_passed", actor="system")


async def test_illegal_trusted_to_sandbox_backward(db: aiosqlite.Connection) -> None:
    """trusted → sandbox: backward, not in table."""
    await _insert_skill(db, state="trusted")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(IllegalTransitionError):
        await mgr.transition("s1", SkillState.SANDBOX, reason="gate_passed", actor="system")


async def test_skill_not_found(db: aiosqlite.Connection) -> None:
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(SkillNotFoundError):
        await mgr.transition(
            "nonexistent", SkillState.TRUSTED, reason="gate_passed", actor="system"
        )


# ---------------------------------------------------------------------------
# requires_human_gate tests
# ---------------------------------------------------------------------------


async def test_human_gate_blocks_system_actor(db: aiosqlite.Connection) -> None:
    """Skill with requires_human_gate=True, actor=system → HumanGateRequiredError."""
    await _insert_skill(db, state="sandbox", requires_human_gate=True)
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(HumanGateRequiredError):
        await mgr.transition(
            "s1", SkillState.SHADOW_PRIMARY, reason="gate_passed", actor="system"
        )


async def test_human_gate_allows_user_actor(db: aiosqlite.Connection) -> None:
    """Same skill, actor=user, reason=human_approval → succeeds."""
    await _insert_skill(db, state="sandbox", requires_human_gate=True)
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition(
        "s1", SkillState.SHADOW_PRIMARY, reason="human_approval", actor="user"
    )
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


async def test_human_gate_blocks_system_even_with_manual_override_reason(
    db: aiosqlite.Connection,
) -> None:
    """requires_human_gate=True blocks actor=system regardless of reason.

    Even reason='manual_override' must not let system bypass the gate — the gate
    is enforced purely on actor identity, not on reason string.
    """
    await _insert_skill(db, state="trusted", requires_human_gate=True)
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(HumanGateRequiredError):
        await mgr.transition(
            "s1",
            SkillState.CLAUDE_NATIVE,
            reason="manual_override",
            actor="system",
        )


# ---------------------------------------------------------------------------
# Audit trail tests
# ---------------------------------------------------------------------------


async def test_audit_row_inserted_after_transition(db: aiosqlite.Connection) -> None:
    """After a successful transition, a skill_state_transition row exists with correct fields."""
    await _insert_skill(db, state="sandbox")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition(
        "s1",
        SkillState.SHADOW_PRIMARY,
        reason="gate_passed",
        actor="system",
        actor_id=None,
        notes="promoted by evaluator",
    )

    cursor = await db.execute(
        "SELECT skill_id, from_state, to_state, reason, actor, notes FROM skill_state_transition"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    skill_id, from_state, to_state, reason, actor, notes = rows[0]
    assert skill_id == "s1"
    assert from_state == "sandbox"
    assert to_state == "shadow_primary"
    assert reason == "gate_passed"
    assert actor == "system"
    assert notes == "promoted by evaluator"


async def test_audit_row_has_id_and_at(db: aiosqlite.Connection) -> None:
    """The audit row has a non-null id and at timestamp."""
    await _insert_skill(db, state="trusted")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition(
        "s1", SkillState.FLAGGED_FOR_REVIEW, reason="degradation", actor="system"
    )

    cursor = await db.execute(
        "SELECT id, at FROM skill_state_transition WHERE skill_id = 's1'"
    )
    row = await cursor.fetchone()
    assert row is not None
    row_id, at_val = row
    assert row_id  # non-empty string (uuid7)
    assert at_val  # non-empty ISO timestamp


async def test_audit_actor_id_stored(db: aiosqlite.Connection) -> None:
    """actor_id is persisted in the audit row when provided."""
    await _insert_skill(db, state="flagged_for_review")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition(
        "s1",
        SkillState.TRUSTED,
        reason="human_approval",
        actor="user",
        actor_id="nick",
    )

    cursor = await db.execute(
        "SELECT actor_id FROM skill_state_transition WHERE skill_id = 's1'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "nick"


async def test_skill_updated_at_changes_after_transition(db: aiosqlite.Connection) -> None:
    """The skill row's updated_at is refreshed after a successful transition."""
    await _insert_skill(db, state="draft")
    cursor = await db.execute("SELECT updated_at FROM skill WHERE id = 's1'")
    original_updated_at = (await cursor.fetchone())[0]

    # Ensure a measurable time delta so the timestamps are strictly different.
    await asyncio.sleep(0.001)

    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    await mgr.transition("s1", SkillState.SANDBOX, reason="human_approval", actor="user")

    cursor = await db.execute("SELECT updated_at FROM skill WHERE id = 's1'")
    new_updated_at = (await cursor.fetchone())[0]
    # updated_at should be a fresh timestamp; at minimum it must be set
    assert new_updated_at is not None
    # The timestamp must have actually advanced past the original value.
    assert new_updated_at != original_updated_at


async def test_no_audit_row_on_illegal_transition(db: aiosqlite.Connection) -> None:
    """No audit row is written when transition is rejected."""
    await _insert_skill(db, state="draft")
    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    with pytest.raises(IllegalTransitionError):
        await mgr.transition("s1", SkillState.TRUSTED, reason="gate_passed", actor="system")

    cursor = await db.execute("SELECT COUNT(*) FROM skill_state_transition")
    row = await cursor.fetchone()
    assert row[0] == 0


# ---------------------------------------------------------------------------
# Auto-promotion gate tests — db_with_runs fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_with_runs(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
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
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL
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
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def _insert_skill_full(
    db: aiosqlite.Connection,
    skill_id: str = "s1",
    state: str = "sandbox",
    requires_human_gate: bool = False,
    baseline_agreement: float | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
        "baseline_agreement, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (skill_id, f"cap-{skill_id}", state, 1 if requires_human_gate else 0,
         baseline_agreement, now, now),
    )
    await db.commit()


async def _insert_skill_runs(
    db: aiosqlite.Connection,
    skill_id: str,
    statuses: list[str],
) -> list[str]:
    """Insert skill_run rows; return list of run IDs."""
    run_ids = []
    for i, status in enumerate(statuses):
        run_id = f"run-{skill_id}-{i}"
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO skill_run (id, skill_id, status, started_at) VALUES (?, ?, ?, ?)",
            (run_id, skill_id, status, now),
        )
        run_ids.append(run_id)
    await db.commit()
    return run_ids


async def _insert_divergences(
    db: aiosqlite.Connection,
    run_ids: list[str],
    agreements: list[float],
) -> None:
    """Insert skill_divergence rows for the given run_ids and agreement values."""
    for i, (run_id, agreement) in enumerate(zip(run_ids, agreements, strict=False)):
        div_id = f"div-{run_id}"
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO skill_divergence "
            "(id, skill_run_id, shadow_invocation_id, overall_agreement, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (div_id, run_id, f"inv-{i}", agreement, now),
        )
    await db.commit()


async def test_promotion_sandbox_to_shadow_primary_fires_with_enough_runs(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """20 succeeded skill_runs for a sandbox skill with validity 1.0 → transitions to shadow_primary."""
    await _insert_skill_full(db_with_runs, state="sandbox")
    await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 20)

    cfg = SkillSystemConfig(sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9)
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result == "shadow_primary"
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


async def test_promotion_sandbox_blocked_by_insufficient_runs(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """10 runs (below min=20) → returns None, state unchanged."""
    await _insert_skill_full(db_with_runs, state="sandbox")
    await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 10)

    cfg = SkillSystemConfig(sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9)
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"


async def test_promotion_sandbox_blocked_by_low_validity_rate(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """20 runs with only 10 succeeded (0.5 rate, below 0.9 default) → returns None."""
    await _insert_skill_full(db_with_runs, state="sandbox")
    statuses = ["succeeded"] * 10 + ["failed"] * 10
    await _insert_skill_runs(db_with_runs, "s1", statuses)

    cfg = SkillSystemConfig(sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9)
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"


async def test_promotion_sandbox_blocked_by_human_gate(
    db_with_runs: aiosqlite.Connection,
    caplog,
) -> None:
    """Eligible skill with requires_human_gate=True → returns None, logs eligibility."""
    await _insert_skill_full(db_with_runs, state="sandbox", requires_human_gate=True)
    await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 20)

    cfg = SkillSystemConfig(sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9)
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"


async def test_promotion_shadow_primary_to_trusted_fires(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """100 divergences averaging 0.9 agreement → transitions to trusted AND baseline_agreement updated."""
    await _insert_skill_full(db_with_runs, state="shadow_primary")
    run_ids = await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 100)
    await _insert_divergences(db_with_runs, run_ids, [0.9] * 100)

    cfg = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
    )
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result == "trusted"
    cursor = await db_with_runs.execute(
        "SELECT state, baseline_agreement FROM skill WHERE id = 's1'"
    )
    row = await cursor.fetchone()
    assert row[0] == "trusted"
    assert abs(row[1] - 0.9) < 1e-9


async def test_promotion_shadow_primary_blocked_by_insufficient_divergences(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """50 divergences (below min=100) → returns None."""
    await _insert_skill_full(db_with_runs, state="shadow_primary")
    run_ids = await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 50)
    await _insert_divergences(db_with_runs, run_ids, [0.9] * 50)

    cfg = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
    )
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


async def test_promotion_shadow_primary_blocked_by_low_agreement(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """100 divergences averaging 0.6 (below 0.85 threshold) → returns None."""
    await _insert_skill_full(db_with_runs, state="shadow_primary")
    run_ids = await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 100)
    await _insert_divergences(db_with_runs, run_ids, [0.6] * 100)

    cfg = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
    )
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db_with_runs.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


async def test_promotion_only_fires_for_sandbox_or_shadow_primary(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """draft/trusted/flagged_for_review skills → no-op returns None."""
    cfg = SkillSystemConfig()
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)

    for state in ("draft", "trusted", "flagged_for_review"):
        skill_id = f"s-{state}"
        await _insert_skill_full(db_with_runs, skill_id=skill_id, state=state)
        result = await mgr.check_and_promote_if_eligible(skill_id)
        assert result is None, f"Expected None for state={state!r}, got {result!r}"


async def test_promotion_writes_audit_row_with_stats(
    db_with_runs: aiosqlite.Connection,
) -> None:
    """After successful sandbox→shadow_primary promotion, audit row notes contain validity_rate."""
    await _insert_skill_full(db_with_runs, state="sandbox")
    await _insert_skill_runs(db_with_runs, "s1", ["succeeded"] * 20)

    cfg = SkillSystemConfig(sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9)
    mgr = SkillLifecycleManager(db_with_runs, config=cfg)
    await mgr.check_and_promote_if_eligible("s1")

    cursor = await db_with_runs.execute(
        "SELECT notes FROM skill_state_transition WHERE skill_id = 's1'"
    )
    row = await cursor.fetchone()
    assert row is not None
    notes = json.loads(row[0])
    assert "validity_rate" in notes
    assert notes["validity_rate"] == 1.0
    assert notes["runs_examined"] == 20
