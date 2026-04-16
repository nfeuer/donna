"""Phase 3 end-to-end integration test.

Verifies the Phase 3 handoff contract:
  H3.1: A claude_native task type with high volume + positive savings gets surfaced
        as a skill_candidate_report row by the nightly detector.
  H3.2: The auto-drafter consumes a top-ranked candidate, generates a draft skill,
        passes fixture validation, creates a skill.state == draft row. Budget respected.
  H3.3: User-driven POST /admin/skills/{id}/state transitions work through
        SkillLifecycleManager.
  H3.4: Auto-promotion from sandbox → shadow_primary fires after enough successful runs.
  H3.5: Auto-promotion from shadow_primary → trusted fires after enough agreeing
        shadow samples.
  H3.6: Statistical degradation triggers transition to flagged_for_review with a
        recorded reason.
  H3.7: requires_human_gate prevents auto-promotion but allows manual promotion.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.auto_drafter import AutoDrafter
from donna.skills.candidate_report import SkillCandidateRepository
from donna.skills.degradation import DegradationDetector
from donna.skills.detector import SkillCandidateDetector
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.lifecycle import (
    HumanGateRequiredError,
    IllegalTransitionError,
    SkillLifecycleManager,
)
from donna.tasks.db_models import SkillState


# ---------------------------------------------------------------------------
# Shared DB fixture
# ---------------------------------------------------------------------------

_PHASE3_SCHEMA = """
    CREATE TABLE capability (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        description TEXT, input_schema TEXT, trigger_type TEXT,
        status TEXT NOT NULL, created_at TEXT NOT NULL,
        created_by TEXT NOT NULL, embedding BLOB
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
    CREATE TABLE skill_version (
        id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        yaml_backbone TEXT NOT NULL, step_content TEXT NOT NULL,
        output_schemas TEXT NOT NULL, created_by TEXT NOT NULL,
        changelog TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE skill_state_transition (
        id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
        from_state TEXT NOT NULL, to_state TEXT NOT NULL,
        reason TEXT NOT NULL, actor TEXT NOT NULL,
        actor_id TEXT, at TEXT NOT NULL, notes TEXT
    );
    CREATE TABLE skill_run (
        id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
        skill_version_id TEXT, task_id TEXT, automation_run_id TEXT,
        status TEXT NOT NULL, total_latency_ms INTEGER,
        total_cost_usd REAL, state_object TEXT NOT NULL,
        tool_result_cache TEXT, final_output TEXT,
        escalation_reason TEXT, error TEXT, user_id TEXT NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT
    );
    CREATE TABLE skill_divergence (
        id TEXT PRIMARY KEY, skill_run_id TEXT NOT NULL,
        shadow_invocation_id TEXT NOT NULL,
        overall_agreement REAL NOT NULL,
        diff_summary TEXT, flagged_for_evolution INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE skill_candidate_report (
        id TEXT PRIMARY KEY, capability_name TEXT,
        task_pattern_hash TEXT, expected_savings_usd REAL NOT NULL,
        volume_30d INTEGER NOT NULL, variance_score REAL,
        status TEXT NOT NULL, reported_at TEXT NOT NULL,
        resolved_at TEXT
    );
    CREATE TABLE invocation_log (
        id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, task_type TEXT NOT NULL,
        task_id TEXT, model_alias TEXT NOT NULL, model_actual TEXT NOT NULL,
        input_hash TEXT NOT NULL, latency_ms INTEGER NOT NULL,
        tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
        cost_usd REAL NOT NULL, output TEXT, quality_score REAL,
        is_shadow INTEGER DEFAULT 0, eval_session_id TEXT,
        spot_check_queued INTEGER DEFAULT 0, user_id TEXT NOT NULL
    );
"""


@pytest.fixture
async def phase3_db(tmp_path: Path):
    db_path = tmp_path / "phase3.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(_PHASE3_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _ts(delta_days: int = 0) -> str:
    return (_NOW - timedelta(days=delta_days)).isoformat()


async def _insert_skill(
    conn: aiosqlite.Connection,
    *,
    skill_id: str,
    capability_name: str,
    state: str,
    requires_human_gate: int = 0,
    baseline_agreement: float | None = None,
) -> None:
    now = _ts()
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
        (skill_id, capability_name, state, requires_human_gate, baseline_agreement, now, now),
    )
    await conn.commit()


async def _insert_skill_run(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    skill_id: str,
    status: str = "succeeded",
) -> None:
    now = _ts()
    await conn.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, task_id, automation_run_id, "
        "status, total_latency_ms, total_cost_usd, state_object, tool_result_cache, "
        "final_output, escalation_reason, error, user_id, started_at, finished_at) "
        "VALUES (?, ?, NULL, NULL, NULL, ?, NULL, NULL, '{}', NULL, NULL, NULL, NULL, 'nick', ?, ?)",
        (run_id, skill_id, status, now, now),
    )
    await conn.commit()


async def _insert_divergence(
    conn: aiosqlite.Connection,
    *,
    div_id: str,
    run_id: str,
    overall_agreement: float,
) -> None:
    now = _ts()
    await conn.execute(
        "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
        "overall_agreement, diff_summary, flagged_for_evolution, created_at) "
        "VALUES (?, ?, 'shadow-inv', ?, NULL, 0, ?)",
        (div_id, run_id, overall_agreement, now),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# H3.1 — candidate detection
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h3_1_detector_surfaces_high_savings_candidate(phase3_db):
    """H3.1: A claude_native task type with high volume + positive savings gets
    surfaced as a skill_candidate_report row by the nightly detector."""
    # Insert 200 invocation_log rows for task_type='parse_task' at $0.10 each,
    # all within the last 30 days. No skill row → claude_native.
    for i in range(200):
        await phase3_db.execute(
            "INSERT INTO invocation_log VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, 0, 'nick')",
            (f"inv-{i}", _ts(1), "parse_task", "parser", "model", "hash", 50, 10, 5, 0.10, '{"x": 1}'),
        )
    await phase3_db.commit()

    candidate_repo = SkillCandidateRepository(phase3_db)
    config = SkillSystemConfig(auto_draft_min_expected_savings_usd=5.0)
    detector = SkillCandidateDetector(phase3_db, candidate_repo, config)

    new_ids = await detector.run()

    assert len(new_ids) == 1

    cands = await candidate_repo.list_new()
    assert len(cands) == 1
    assert cands[0].capability_name == "parse_task"
    # 200 runs * $0.10 * 0.85 (1 - 0.15 overhead) = $17.00
    assert cands[0].expected_savings_usd > 5.0
    assert cands[0].volume_30d == 200
    assert cands[0].status == "new"


@pytest.mark.integration
async def test_h3_1_detector_skips_existing_skill(phase3_db):
    """H3.1 corollary: detector skips task types that already have a non-claude_native skill."""
    for i in range(200):
        await phase3_db.execute(
            "INSERT INTO invocation_log VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL, 0, 'nick')",
            (f"inv-{i}", _ts(1), "parse_task", "parser", "model", "hash", 50, 10, 5, 0.10, '{"x": 1}'),
        )
    # skill row exists in sandbox state (non-claude_native)
    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="sandbox")

    candidate_repo = SkillCandidateRepository(phase3_db)
    config = SkillSystemConfig(auto_draft_min_expected_savings_usd=5.0)
    detector = SkillCandidateDetector(phase3_db, candidate_repo, config)

    new_ids = await detector.run()

    # No new candidates because parse_task is already a non-native skill
    assert new_ids == []


# ---------------------------------------------------------------------------
# H3.2 — auto-drafter happy path
# ---------------------------------------------------------------------------

_GOOD_SKILL_YAML = """
capability_name: parse_task
version: 1
steps:
  - name: extract
    kind: llm
    prompt: extract.md
    output_schema: extract.json
final_output: "{{ state.extract }}"
"""

_GOOD_DRAFT_PAYLOAD = {
    "skill_yaml": _GOOD_SKILL_YAML,
    "step_prompts": {"extract": "Extract task fields from: {{ inputs.raw }}"},
    "output_schemas": {"extract": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
    "fixtures": [
        {"case_name": "basic", "input": {"raw": "fix the bug"}, "expected_output_shape": {"title": "string"}},
        {"case_name": "complex", "input": {"raw": "schedule a meeting"}, "expected_output_shape": {"title": "string"}},
        {"case_name": "edge", "input": {"raw": ""}, "expected_output_shape": {"title": "string"}},
    ],
}


@pytest.mark.integration
async def test_h3_2_auto_drafter_happy_path(phase3_db):
    """H3.2: auto-drafter consumes a top-ranked candidate, creates skill.state=draft.
    Verifies: outcome==drafted, skill.state==draft, candidate marked drafted,
    two skill_state_transition rows (claude_native→skill_candidate, skill_candidate→draft).
    """
    # Insert a capability row required by the drafter
    await phase3_db.execute(
        "INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        ("cap-1", "parse_task", "Parse task from text", '{"type": "object"}', "on_message", _ts()),
    )
    # Insert a candidate in 'new' state
    candidate_repo = SkillCandidateRepository(phase3_db)
    candidate_id = await candidate_repo.create(
        capability_name="parse_task",
        task_pattern_hash=None,
        expected_savings_usd=20.0,
        volume_30d=200,
        variance_score=0.8,
    )

    config = SkillSystemConfig(auto_draft_fixture_pass_rate=0.80)
    lifecycle = SkillLifecycleManager(phase3_db, config)

    # Mock router to return well-formed payload
    router = AsyncMock()
    meta = MagicMock(invocation_id="i1", latency_ms=200, tokens_in=100, tokens_out=50, cost_usd=0.30)
    router.complete = AsyncMock(return_value=(_GOOD_DRAFT_PAYLOAD, meta))

    # Mock executor_factory: executor.execute returns a succeeded result with matching output
    mock_run_result = MagicMock()
    mock_run_result.status = "succeeded"
    mock_run_result.final_output = {"title": "test task"}
    mock_run_result.error = None
    mock_run_result.escalation_reason = None

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=mock_run_result)

    def executor_factory():
        return mock_executor

    budget_guard = AsyncMock()

    drafter = AutoDrafter(
        connection=phase3_db,
        model_router=router,
        budget_guard=budget_guard,
        candidate_repo=candidate_repo,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=executor_factory,
        estimated_draft_cost_usd=0.50,
    )

    reports = await drafter.run(remaining_budget_usd=10.0, max_drafts=1)

    assert len(reports) == 1
    report = reports[0]
    assert report.outcome == "drafted", f"expected drafted, got {report.outcome}: {report.rationale}"
    assert report.skill_id is not None
    assert report.pass_rate is not None and report.pass_rate >= 0.80

    # Verify skill is in draft state
    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = ?", (report.skill_id,))
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "draft"

    # Verify candidate marked drafted
    cursor = await phase3_db.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?", (candidate_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "drafted"

    # Verify two transition rows: claude_native→skill_candidate, skill_candidate→draft
    cursor = await phase3_db.execute(
        "SELECT from_state, to_state FROM skill_state_transition WHERE skill_id = ? ORDER BY at",
        (report.skill_id,),
    )
    transitions = await cursor.fetchall()
    assert len(transitions) == 2
    assert transitions[0] == ("claude_native", "skill_candidate")
    assert transitions[1] == ("skill_candidate", "draft")


@pytest.mark.integration
async def test_h3_2_budget_exhausted_stops_early(phase3_db):
    """H3.2: auto-drafter respects budget and does not draft when budget is too low."""
    await phase3_db.execute(
        "INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        ("cap-1", "parse_task", "Parse task from text", '{"type": "object"}', "on_message", _ts()),
    )
    candidate_repo = SkillCandidateRepository(phase3_db)
    await candidate_repo.create(
        capability_name="parse_task",
        task_pattern_hash=None,
        expected_savings_usd=20.0,
        volume_30d=200,
        variance_score=0.8,
    )

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase3_db, config)
    router = AsyncMock()

    drafter = AutoDrafter(
        connection=phase3_db,
        model_router=router,
        budget_guard=AsyncMock(),
        candidate_repo=candidate_repo,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=lambda: MagicMock(),
        estimated_draft_cost_usd=5.0,
    )

    # Pass remaining_budget_usd less than estimated_draft_cost_usd
    reports = await drafter.run(remaining_budget_usd=0.10, max_drafts=5)

    # No drafts attempted — stopped early
    assert reports == []
    # Router never called
    router.complete.assert_not_called()


# ---------------------------------------------------------------------------
# H3.3 — POST /admin/skills/{id}/state via route function
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h3_3_post_state_transition_happy_path(phase3_db):
    """H3.3: User-driven state transition: draft → sandbox succeeds, skill row updated."""
    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="draft")

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase3_db, config)

    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    request = MagicMock()
    request.app.state.db.connection = phase3_db
    request.app.state.skill_lifecycle_manager = lifecycle

    body = TransitionRequest(to_state="sandbox", reason="human_approval")
    result = await transition_skill_state(skill_id="s1", body=body, request=request)

    assert result["ok"] is True
    assert result["to_state"] == "sandbox"

    # Verify DB updated
    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"

    # Verify transition audit row
    cursor = await phase3_db.execute(
        "SELECT from_state, to_state, actor FROM skill_state_transition WHERE skill_id = 's1'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0] == ("draft", "sandbox", "user")


@pytest.mark.integration
async def test_h3_3_post_illegal_transition_returns_400(phase3_db):
    """H3.3: Illegal transition (sandbox → trusted) returns 400."""
    from fastapi import HTTPException

    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="sandbox")

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase3_db, config)

    from donna.api.routes.skills import TransitionRequest, transition_skill_state

    request = MagicMock()
    request.app.state.db.connection = phase3_db
    request.app.state.skill_lifecycle_manager = lifecycle

    body = TransitionRequest(to_state="trusted", reason="human_approval")
    with pytest.raises(HTTPException) as excinfo:
        await transition_skill_state(skill_id="s1", body=body, request=request)

    assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# H3.4 — sandbox → shadow_primary auto-promotion
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h3_4_sandbox_auto_promotes_to_shadow_primary(phase3_db):
    """H3.4: After 20 succeeded runs, sandbox skill auto-promotes to shadow_primary."""
    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="sandbox")

    # Insert 20 succeeded skill_run rows
    for i in range(20):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")

    config = SkillSystemConfig(
        sandbox_promotion_min_runs=20,
        sandbox_promotion_validity_rate=0.90,
    )
    lifecycle = SkillLifecycleManager(phase3_db, config)

    new_state = await lifecycle.check_and_promote_if_eligible("s1")

    assert new_state == "shadow_primary"

    # Verify DB
    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"

    # Verify audit row
    cursor = await phase3_db.execute(
        "SELECT from_state, to_state, actor FROM skill_state_transition WHERE skill_id = 's1'"
    )
    transitions = await cursor.fetchall()
    assert len(transitions) == 1
    assert transitions[0] == ("sandbox", "shadow_primary", "system")


@pytest.mark.integration
async def test_h3_4_sandbox_no_promotion_with_insufficient_runs(phase3_db):
    """H3.4 corollary: fewer than min_runs → no promotion."""
    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="sandbox")

    for i in range(10):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")

    config = SkillSystemConfig(sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.90)
    lifecycle = SkillLifecycleManager(phase3_db, config)

    new_state = await lifecycle.check_and_promote_if_eligible("s1")
    assert new_state is None

    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"


# ---------------------------------------------------------------------------
# H3.5 — shadow_primary → trusted auto-promotion
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h3_5_shadow_primary_auto_promotes_to_trusted(phase3_db):
    """H3.5: After 100 divergences with mean agreement >= 0.9, shadow_primary → trusted."""
    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="shadow_primary")

    # Insert 100 skill_run rows and 100 divergence rows with high agreement
    for i in range(100):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")
        await _insert_divergence(
            phase3_db,
            div_id=f"div-{i}",
            run_id=f"run-{i}",
            overall_agreement=0.95,
        )

    config = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
    )
    lifecycle = SkillLifecycleManager(phase3_db, config)

    new_state = await lifecycle.check_and_promote_if_eligible("s1")

    assert new_state == "trusted"

    # Verify DB state
    cursor = await phase3_db.execute("SELECT state, baseline_agreement FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "trusted"
    assert row[1] is not None
    assert abs(row[1] - 0.95) < 0.01

    # Verify audit row
    cursor = await phase3_db.execute(
        "SELECT from_state, to_state, actor FROM skill_state_transition WHERE skill_id = 's1'"
    )
    transitions = await cursor.fetchall()
    assert len(transitions) == 1
    assert transitions[0] == ("shadow_primary", "trusted", "system")


@pytest.mark.integration
async def test_h3_5_shadow_primary_no_promotion_low_agreement(phase3_db):
    """H3.5 corollary: mean agreement below threshold → no promotion."""
    await _insert_skill(phase3_db, skill_id="s1", capability_name="parse_task", state="shadow_primary")

    for i in range(100):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")
        await _insert_divergence(
            phase3_db,
            div_id=f"div-{i}",
            run_id=f"run-{i}",
            overall_agreement=0.50,  # well below 0.85 threshold
        )

    config = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
    )
    lifecycle = SkillLifecycleManager(phase3_db, config)

    new_state = await lifecycle.check_and_promote_if_eligible("s1")
    assert new_state is None

    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"


# ---------------------------------------------------------------------------
# H3.6 — statistical degradation triggers flagged_for_review
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h3_6_degradation_flags_trusted_skill(phase3_db):
    """H3.6: Trusted skill with baseline=0.9 and recent avg agreement ~0.5 → flagged_for_review."""
    await _insert_skill(
        phase3_db,
        skill_id="s1",
        capability_name="parse_task",
        state="trusted",
        baseline_agreement=0.9,
    )

    # Insert 30 run + divergence rows with low agreement (0.40, all below the 0.5
    # success threshold used by DegradationDetector → 0/30 successes → CI upper ~0.11,
    # well below baseline 0.9).
    for i in range(30):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")
        await _insert_divergence(
            phase3_db,
            div_id=f"div-{i}",
            run_id=f"run-{i}",
            overall_agreement=0.40,
        )

    config = SkillSystemConfig(
        degradation_rolling_window=30,
        degradation_ci_confidence=0.95,
    )
    lifecycle = SkillLifecycleManager(phase3_db, config)
    divergence_repo = SkillDivergenceRepository(phase3_db)
    detector = DegradationDetector(phase3_db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    assert len(reports) == 1
    report = reports[0]
    assert report.skill_id == "s1"
    assert report.outcome == "flagged"
    assert report.notes is not None

    # Verify skill transitioned to flagged_for_review
    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "flagged_for_review"

    # Verify audit row contains degradation reason
    cursor = await phase3_db.execute(
        "SELECT reason, notes FROM skill_state_transition WHERE skill_id = 's1'"
    )
    transition = await cursor.fetchone()
    assert transition is not None
    assert transition[0] == "degradation"
    # notes should contain CI and baseline info
    notes_data = json.loads(transition[1])
    assert "baseline_agreement" in notes_data
    assert notes_data["baseline_agreement"] == 0.9


@pytest.mark.integration
async def test_h3_6_no_degradation_when_agreement_still_high(phase3_db):
    """H3.6 corollary: trusted skill with still-high agreement is NOT flagged."""
    await _insert_skill(
        phase3_db,
        skill_id="s1",
        capability_name="parse_task",
        state="trusted",
        baseline_agreement=0.85,
    )

    for i in range(30):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")
        await _insert_divergence(
            phase3_db,
            div_id=f"div-{i}",
            run_id=f"run-{i}",
            overall_agreement=0.93,  # still above baseline
        )

    config = SkillSystemConfig(degradation_rolling_window=30)
    lifecycle = SkillLifecycleManager(phase3_db, config)
    divergence_repo = SkillDivergenceRepository(phase3_db)
    detector = DegradationDetector(phase3_db, divergence_repo, lifecycle, config)

    reports = await detector.run()

    assert reports[0].outcome == "no_degradation"

    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "trusted"


# ---------------------------------------------------------------------------
# H3.7 — requires_human_gate blocks auto-promotion, allows manual
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_h3_7_human_gate_blocks_auto_promotion(phase3_db):
    """H3.7: requires_human_gate=True prevents auto-promotion even when gate criteria met."""
    await _insert_skill(
        phase3_db,
        skill_id="s1",
        capability_name="parse_task",
        state="sandbox",
        requires_human_gate=1,
    )

    # Insert 20 succeeded runs — normally enough to trigger promotion
    for i in range(20):
        await _insert_skill_run(phase3_db, run_id=f"run-{i}", skill_id="s1", status="succeeded")

    config = SkillSystemConfig(
        sandbox_promotion_min_runs=20,
        sandbox_promotion_validity_rate=0.90,
    )
    lifecycle = SkillLifecycleManager(phase3_db, config)

    new_state = await lifecycle.check_and_promote_if_eligible("s1")

    # Gate blocks promotion — returns None, state unchanged
    assert new_state is None

    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"

    # Verify no transition audit rows created
    cursor = await phase3_db.execute(
        "SELECT COUNT(*) FROM skill_state_transition WHERE skill_id = 's1'"
    )
    row = await cursor.fetchone()
    assert row[0] == 0


@pytest.mark.integration
async def test_h3_7_human_gate_allows_manual_promotion(phase3_db):
    """H3.7: requires_human_gate=True allows manual (actor=user) promotion."""
    await _insert_skill(
        phase3_db,
        skill_id="s1",
        capability_name="parse_task",
        state="sandbox",
        requires_human_gate=1,
    )

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase3_db, config)

    # Manual transition with actor="user" and valid reason should succeed
    await lifecycle.transition(
        skill_id="s1",
        to_state=SkillState.SHADOW_PRIMARY,
        reason="human_approval",
        actor="user",
        notes="manual approval by nick",
    )

    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"

    cursor = await phase3_db.execute(
        "SELECT actor, notes FROM skill_state_transition WHERE skill_id = 's1'"
    )
    row = await cursor.fetchone()
    assert row[0] == "user"
    assert "nick" in row[1]


@pytest.mark.integration
async def test_h3_7_system_actor_raises_human_gate_required(phase3_db):
    """H3.7: system actor is rejected with HumanGateRequiredError when gate is set."""
    await _insert_skill(
        phase3_db,
        skill_id="s1",
        capability_name="parse_task",
        state="sandbox",
        requires_human_gate=1,
    )

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase3_db, config)

    with pytest.raises(HumanGateRequiredError):
        await lifecycle.transition(
            skill_id="s1",
            to_state=SkillState.SHADOW_PRIMARY,
            reason="gate_passed",
            actor="system",
        )

    # State must remain unchanged
    cursor = await phase3_db.execute("SELECT state FROM skill WHERE id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == "sandbox"
