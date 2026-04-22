from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError
from donna.skills.evolution import Evolver


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
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
        CREATE TABLE skill_state_transition (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            from_state TEXT NOT NULL, to_state TEXT NOT NULL,
            reason TEXT NOT NULL, actor TEXT NOT NULL,
            actor_id TEXT, at TEXT NOT NULL, notes TEXT
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


async def _seed_degraded_skill(
    db, *, skill_id="s1", n_divergences=20, requires_human_gate=False,
):
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'demo', 'demo cap', '{}', 'on_message', 'active', ?, 'seed')",
        (now,),
    )
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, 'demo', 'v1', 'degraded', ?, 0.9, ?, ?)",
        (skill_id, 1 if requires_human_gate else 0, now, now),
    )
    await db.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, "
        "yaml_backbone, step_content, output_schemas, created_by, "
        "changelog, created_at) VALUES "
        "('v1', ?, 1, 'capability_name: demo\\nversion: 1\\nsteps: []\\n', "
        "'{}', '{}', 'seed', 'v1', ?)",
        (skill_id, now),
    )
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
        "state_object, user_id, started_at) VALUES "
        "('r1', ?, 'v1', 'succeeded', '{}', 'nick', ?)",
        (skill_id, now),
    )
    for i in range(n_divergences):
        await db.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
            "overall_agreement, diff_summary, created_at) VALUES "
            "(?, 'r1', ?, 0.4, '{}', ?)",
            (f"d{i}", f"inv{i}", now),
        )
    await db.commit()


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


def _mock_lifecycle():
    lifecycle = MagicMock()
    lifecycle.transition = AsyncMock()
    return lifecycle


def _mock_executor_always_succeeds():
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(status="succeeded"))
    return executor


async def test_evolve_happy_path_persists_new_version(db):
    await _seed_degraded_skill(db)
    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-9"))
    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock()
    lifecycle = _mock_lifecycle()
    executor = _mock_executor_always_succeeds()

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=lambda: executor,
    )

    report = await evolver.evolve_one(skill_id="s1", triggered_by="statistical_degradation")

    assert report.outcome == "success"
    assert report.new_version_id is not None
    # Log row written.
    cursor = await db.execute("SELECT outcome FROM skill_evolution_log WHERE skill_id = 's1'")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "success"
    # New version row exists.
    cursor = await db.execute("SELECT COUNT(*) FROM skill_version WHERE skill_id = 's1'")
    row = await cursor.fetchone()
    assert row[0] == 2
    # Lifecycle transitioned the skill (to draft or sandbox).
    assert lifecycle.transition.await_count >= 1


async def test_evolve_requires_human_gate_lands_in_draft(db):
    await _seed_degraded_skill(db, requires_human_gate=True)
    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-9"))
    budget_guard = AsyncMock()
    lifecycle = _mock_lifecycle()

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=_mock_executor_always_succeeds,
    )

    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")
    assert report.outcome == "success"
    # Lifecycle was called with to_state=draft (gated skill) not sandbox.
    last_call = lifecycle.transition.await_args_list[-1]
    assert last_call.kwargs["to_state"].value == "draft"


async def test_evolve_malformed_output_marks_rejected(db):
    await _seed_degraded_skill(db)
    router = AsyncMock()
    router.complete.return_value = ({"foo": "bar"}, MagicMock(invocation_id="inv-x"))
    budget_guard = AsyncMock()
    lifecycle = _mock_lifecycle()

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=_mock_executor_always_succeeds,
    )
    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")
    assert report.outcome == "rejected_validation"
    assert "malformed" in (report.rationale or "")


async def test_evolve_validation_failure_marks_rejected(db):
    await _seed_degraded_skill(db)
    # Seed 10 skill_fixture rows so fixture gate can meaningfully fail.
    now = datetime.now(UTC).isoformat()
    for i in range(10):
        await db.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, created_at) VALUES "
            "(?, 's1', ?, '{}', NULL, 'seed', ?)",
            (f"f{i}", f"case{i}", now),
        )
    await db.commit()

    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-x"))
    budget_guard = AsyncMock()
    lifecycle = _mock_lifecycle()

    # Executor that always fails → fixture pass rate = 0.
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(status="failed"))

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=lambda: executor,
    )

    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")
    assert report.outcome == "rejected_validation"
    # Skill should remain in degraded (no demotion on first rejection).
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "degraded"


async def test_two_consecutive_failures_demote_to_claude_native(db):
    await _seed_degraded_skill(db)
    # Pre-seed one rejected_validation in the log.
    import uuid6
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_evolution_log (id, skill_id, from_version_id, "
        "to_version_id, triggered_by, claude_invocation_id, diagnosis, "
        "targeted_case_ids, validation_results, outcome, at) VALUES "
        "(?, 's1', 'v1', NULL, 'statistical_degradation', NULL, NULL, "
        "NULL, NULL, 'rejected_validation', ?)",
        (str(uuid6.uuid7()), now),
    )
    await db.commit()

    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-x"))
    budget_guard = AsyncMock()
    lifecycle = _mock_lifecycle()
    # Executor that fails so the second evolution also rejects validation.
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(status="failed"))
    # Seed one fixture so the fixture gate runs.
    await db.execute(
        "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
        "expected_output_shape, source, created_at) VALUES "
        "('f1', 's1', 'c', '{}', NULL, 'seed', ?)",
        (now,),
    )
    await db.commit()

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=lambda: executor,
    )

    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")
    assert report.outcome == "rejected_validation"
    # Should have called lifecycle.transition(... to=claude_native, reason=evolution_failed).
    transitions = lifecycle.transition.await_args_list
    demotion_calls = [
        c for c in transitions
        if c.kwargs.get("to_state").value == "claude_native"
        and c.kwargs.get("reason") == "evolution_failed"
    ]
    assert len(demotion_calls) == 1


async def test_evolve_budget_paused_returns_early(db):
    await _seed_degraded_skill(db)
    router = AsyncMock()
    budget_guard = AsyncMock()
    budget_guard.check_pre_call.side_effect = BudgetPausedError(daily_spent=30.0, daily_limit=20.0)
    lifecycle = _mock_lifecycle()

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=_mock_executor_always_succeeds,
    )

    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")
    assert report.outcome == "budget_exhausted"
    # No log row, no transition.
    cursor = await db.execute("SELECT COUNT(*) FROM skill_evolution_log")
    assert (await cursor.fetchone())[0] == 0
    lifecycle.transition.assert_not_awaited()


async def test_evolve_skill_not_in_degraded_state_skips(db):
    await _seed_degraded_skill(db)
    # Flip to trusted.
    await db.execute("UPDATE skill SET state = 'trusted' WHERE id = 's1'")
    await db.commit()

    router = AsyncMock()
    budget_guard = AsyncMock()
    lifecycle = _mock_lifecycle()
    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=_mock_executor_always_succeeds,
    )
    report = await evolver.evolve_one(skill_id="s1", triggered_by="manual")
    assert report.outcome == "skipped"
    router.complete.assert_not_awaited()


async def test_evolve_includes_fixture_library_in_prompt(db):
    await _seed_degraded_skill(db)
    # Seed 2 fixtures for skill s1.
    now = datetime.now(UTC).isoformat()
    for i in range(2):
        await db.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, created_at) VALUES "
            "(?, 's1', ?, '{\"x\": 1}', '{\"type\": \"object\"}', 'seed', ?)",
            (f"f{i}", f"case{i}", now),
        )
    await db.commit()

    router = AsyncMock()
    router.complete.return_value = (_valid_llm_output(), MagicMock(invocation_id="inv-x"))
    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock()
    lifecycle = _mock_lifecycle()

    evolver = Evolver(
        connection=db, model_router=router, budget_guard=budget_guard,
        lifecycle_manager=lifecycle, config=SkillSystemConfig(),
        executor_factory=_mock_executor_always_succeeds,
    )
    await evolver.evolve_one(skill_id="s1", triggered_by="manual")

    assert router.complete.await_count == 1
    prompt = router.complete.await_args.kwargs["prompt"]
    assert "Fixture library" in prompt or "fixture_library" in prompt
    assert "case0" in prompt or "case1" in prompt
