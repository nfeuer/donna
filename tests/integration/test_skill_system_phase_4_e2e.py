"""Phase 4 end-to-end integration test.

Verifies spec Phase 4 handoff contract (AS-4.1 through AS-4.5):
  AS-4.1: DegradationDetector flags trusted skill with low agreement.
  AS-4.2: POST /state to trusted with reason=human_approval resets baseline.
  AS-4.3: Approve evolution -> degraded -> Evolver produces new version.
  AS-4.4: Two consecutive rejected_validation outcomes -> claude_native.
  AS-4.5: CorrectionClusterDetector flags trusted skill immediately.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import uuid6

from donna.config import SkillSystemConfig
from donna.skills.correction_cluster import CorrectionClusterDetector
from donna.skills.degradation import DegradationDetector
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.evolution import Evolver
from donna.skills.lifecycle import SkillLifecycleManager


@pytest.fixture
async def phase4_db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "phase4.db"))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT, input_schema TEXT, trigger_type TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, embedding BLOB
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT NOT NULL UNIQUE,
            current_version_id TEXT, state TEXT NOT NULL,
            requires_human_gate INTEGER NOT NULL DEFAULT 0,
            baseline_agreement REAL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            version_number INTEGER NOT NULL, yaml_backbone TEXT NOT NULL,
            step_content TEXT NOT NULL, output_schemas TEXT NOT NULL,
            created_by TEXT NOT NULL, changelog TEXT, created_at TEXT NOT NULL
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
        CREATE TABLE skill_fixture (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            case_name TEXT NOT NULL, input TEXT NOT NULL,
            expected_output_shape TEXT, source TEXT NOT NULL,
            captured_run_id TEXT, created_at TEXT NOT NULL,
            tool_mocks TEXT
        );
        CREATE TABLE skill_evolution_log (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            from_version_id TEXT NOT NULL, to_version_id TEXT,
            triggered_by TEXT NOT NULL, claude_invocation_id TEXT,
            diagnosis TEXT, targeted_case_ids TEXT,
            validation_results TEXT, outcome TEXT NOT NULL,
            at TEXT NOT NULL
        );
        CREATE TABLE correction_log (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            user_id TEXT NOT NULL, task_type TEXT NOT NULL,
            task_id TEXT NOT NULL, input_text TEXT NOT NULL,
            field_corrected TEXT NOT NULL, original_value TEXT NOT NULL,
            corrected_value TEXT NOT NULL, rule_extracted TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _valid_llm_output() -> dict:
    return {
        "diagnosis": {
            "identified_failure_step": "extract",
            "failure_pattern": "missing title",
            "confidence": 0.85,
        },
        "new_skill_version": {
            "yaml_backbone": (
                "capability_name: demo\n"
                "version: 2\n"
                "steps:\n"
                "  - name: extract\n"
                "    kind: llm\n"
                "    prompt: steps/extract.md\n"
                "    output_schema: schemas/extract_v1.json\n"
            ),
            "step_content": {"extract": "Extract: {{ inputs.text }}"},
            "output_schemas": {
                "extract": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                }
            },
        },
        "changelog": "clarify title extraction",
        "targeted_failure_cases": [],
        "expected_improvement": "↑ agreement on noisy inputs",
    }


async def _seed_skill(conn, *, skill_id, state, baseline_agreement=0.9):
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT OR REPLACE INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "(?, ?, 'cap', '{}', 'on_message', 'active', ?, 'seed')",
        (f"cap-{skill_id}", f"cap-{skill_id}", now),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, 'v1', ?, 0, ?, ?, ?)",
        (skill_id, f"cap-{skill_id}", state, baseline_agreement, now, now),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, "
        "yaml_backbone, step_content, output_schemas, created_by, "
        "changelog, created_at) VALUES "
        "('v1', ?, 1, 'capability_name: demo\\nversion: 1\\nsteps: []\\n', "
        "'{}', '{}', 'seed', 'v1', ?)",
        (skill_id, now),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# AS-4.1: DegradationDetector flags trusted skill with low-agreement shadows.
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_as_4_1_degradation_flags_skill(phase4_db):
    await _seed_skill(phase4_db, skill_id="s1", state="trusted",
                      baseline_agreement=0.9)

    now = datetime.now(UTC).isoformat()
    await phase4_db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'nick', ?)",
        (now,),
    )
    # 30 divergences all with agreement 0.4 (below 0.5 threshold).
    for i in range(30):
        await phase4_db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.4, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    await phase4_db.commit()

    divergence_repo = SkillDivergenceRepository(phase4_db)
    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase4_db, config)
    detector = DegradationDetector(
        connection=phase4_db,
        divergence_repo=divergence_repo,
        lifecycle_manager=lifecycle,
        config=config,
    )

    reports = await detector.run()
    flagged = [r for r in reports if r.outcome == "flagged"]
    assert len(flagged) == 1

    cursor = await phase4_db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "flagged_for_review"


# ---------------------------------------------------------------------------
# AS-4.2: Save (reset baseline) — flagged_for_review → trusted with
# reason=human_approval updates baseline_agreement.
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_as_4_2_save_resets_baseline(phase4_db):
    # This test exercises the SkillLifecycleManager transition directly plus
    # the same SQL used by the API route to recompute baseline.
    await _seed_skill(phase4_db, skill_id="s1", state="flagged_for_review",
                      baseline_agreement=0.95)

    now = datetime.now(UTC).isoformat()
    await phase4_db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'u', ?)",
        (now,),
    )
    for i in range(10):
        await phase4_db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.82, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    await phase4_db.commit()

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase4_db, config)

    from donna.tasks.db_models import SkillState
    await lifecycle.transition(
        skill_id="s1",
        to_state=SkillState.TRUSTED,
        reason="human_approval",
        actor="user",
        notes="save reset baseline",
    )

    # Recompute baseline (same SQL as in the API route).
    cursor = await phase4_db.execute(
        "SELECT AVG(agreement) FROM (SELECT d.overall_agreement AS agreement "
        "FROM skill_divergence d JOIN skill_run r ON d.skill_run_id = r.id "
        "WHERE r.skill_id = ? ORDER BY d.created_at DESC LIMIT 100)",
        ("s1",),
    )
    new_baseline = (await cursor.fetchone())[0]
    await phase4_db.execute(
        "UPDATE skill SET baseline_agreement = ? WHERE id = 's1'",
        (float(new_baseline),),
    )
    await phase4_db.commit()

    cursor = await phase4_db.execute(
        "SELECT state, baseline_agreement FROM skill WHERE id = 's1'"
    )
    row = await cursor.fetchone()
    assert row[0] == "trusted"
    assert row[1] == pytest.approx(0.82, abs=0.01)


# ---------------------------------------------------------------------------
# AS-4.3: Approve evolution -> degraded. Evolver produces a new version.
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_as_4_3_approve_evolution_generates_new_version(phase4_db):
    await _seed_skill(phase4_db, skill_id="s1", state="flagged_for_review",
                      baseline_agreement=0.9)
    # Seed 20 divergences for the input builder.
    now = datetime.now(UTC).isoformat()
    await phase4_db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'u', ?)",
        (now,),
    )
    for i in range(20):
        await phase4_db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.4, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    await phase4_db.commit()

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase4_db, config)

    # User approves evolution: flagged_for_review -> degraded.
    from donna.tasks.db_models import SkillState
    await lifecycle.transition(
        skill_id="s1", to_state=SkillState.DEGRADED,
        reason="human_approval", actor="user",
    )

    # Evolver runs.
    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-9"))
    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock()
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(status="succeeded"))

    evolver = Evolver(
        connection=phase4_db,
        model_router=router,
        budget_guard=budget_guard,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=lambda: executor,
    )
    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")

    assert report.outcome == "success"
    assert report.new_version_id is not None
    # Skill moved to draft (sandbox transition requires human_approval which
    # system actor cannot supply with gate_passed reason).
    cursor = await phase4_db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "draft"


# ---------------------------------------------------------------------------
# AS-4.4: Two consecutive rejected_validation outcomes -> claude_native.
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_as_4_4_two_failures_demote_to_claude_native(phase4_db):
    await _seed_skill(phase4_db, skill_id="s1", state="degraded")
    # Seed prior rejection in the log.
    now = datetime.now(UTC).isoformat()
    await phase4_db.execute(
        "INSERT INTO skill_evolution_log (id, skill_id, from_version_id, "
        "to_version_id, triggered_by, claude_invocation_id, diagnosis, "
        "targeted_case_ids, validation_results, outcome, at) VALUES "
        "(?, 's1', 'v1', NULL, 'nightly', NULL, NULL, NULL, NULL, "
        "'rejected_validation', ?)",
        (str(uuid6.uuid7()), now),
    )
    # Seed skill_run + divergences for the input builder.
    await phase4_db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', 's1', 'v1', 'succeeded', '{}', 'u', ?)",
        (now,),
    )
    for i in range(20):
        await phase4_db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.4, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    # Seed one fixture so the fixture gate runs.
    await phase4_db.execute(
        "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
        "expected_output_shape, source, created_at) VALUES "
        "('f1', 's1', 'c', '{}', NULL, 'seed', ?)",
        (now,),
    )
    await phase4_db.commit()

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase4_db, config)

    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-fail"))
    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock()
    # Executor that always fails the fixture gate.
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(status="failed"))

    evolver = Evolver(
        connection=phase4_db,
        model_router=router,
        budget_guard=budget_guard,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=lambda: executor,
    )
    report = await evolver.evolve_one(skill_id="s1", triggered_by="nightly")

    assert report.outcome == "rejected_validation"
    cursor = await phase4_db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "claude_native"


# ---------------------------------------------------------------------------
# AS-4.5: Correction clustering flags trusted skill immediately.
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_as_4_5_correction_cluster_flags_immediately(phase4_db):
    await _seed_skill(phase4_db, skill_id="s1", state="trusted")

    now = datetime.now(UTC).isoformat()
    for i in range(10):
        await phase4_db.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, user_id, started_at) VALUES "
            "(?, 's1', 'v1', 'succeeded', '{}', 'nick', ?)",
            (f"r{i}", now),
        )
    for i in range(3):
        await phase4_db.execute(
            "INSERT INTO correction_log (id, timestamp, user_id, task_type, "
            "task_id, input_text, field_corrected, original_value, "
            "corrected_value) VALUES (?, ?, 'nick', 'cap-s1', ?, "
            "'x', 'title', 'a', 'b')",
            (f"c{i}", now, f"r{i}"),
        )
    await phase4_db.commit()

    config = SkillSystemConfig()
    lifecycle = SkillLifecycleManager(phase4_db, config)
    notifier = AsyncMock()
    detector = CorrectionClusterDetector(
        connection=phase4_db, lifecycle_manager=lifecycle,
        notifier=notifier, config=config,
    )
    flagged = await detector.scan_once()
    assert len(flagged) == 1
    assert flagged[0]["skill_id"] == "s1"
    notifier.assert_awaited_once()

    cursor = await phase4_db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "flagged_for_review"
