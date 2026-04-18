"""Tests for AutoDrafter — nightly Claude-driven skill generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError
from donna.skills.auto_drafter import AutoDrafter, AutoDraftReport
from donna.skills.candidate_report import SkillCandidateRepository
from donna.skills.fixtures import FixtureValidationReport
from donna.skills.lifecycle import SkillLifecycleManager


# ---------------------------------------------------------------------------
# DB fixture with the full schema AutoDrafter touches
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript(
        """
        CREATE TABLE capability (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            input_schema TEXT,
            trigger_type TEXT NOT NULL,
            default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            embedding BLOB,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            notes TEXT
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
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            yaml_backbone TEXT NOT NULL,
            step_content TEXT NOT NULL,
            output_schemas TEXT NOT NULL,
            created_by TEXT NOT NULL,
            changelog TEXT,
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
            reasoning TEXT
        );
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
        CREATE TABLE skill_fixture (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            case_name TEXT NOT NULL,
            input TEXT NOT NULL,
            expected_output_shape TEXT,
            source TEXT NOT NULL,
            captured_run_id TEXT,
            created_at TEXT NOT NULL,
            tool_mocks TEXT
        );
        """
    )
    await conn.commit()
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_capability(
    conn: aiosqlite.Connection,
    name: str,
    description: str = "test capability",
    input_schema: str = '{"type": "object"}',
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """
        INSERT INTO capability
            (id, name, description, input_schema, trigger_type, status, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"cap-{name}", name, description, input_schema, "on_message", "active", now, "seed"),
    )
    await conn.commit()


async def _insert_candidate(
    repo: SkillCandidateRepository,
    capability_name: str,
    savings: float = 20.0,
) -> str:
    return await repo.create(
        capability_name=capability_name,
        task_pattern_hash=None,
        expected_savings_usd=savings,
        volume_30d=200,
        variance_score=0.2,
    )


def _well_formed_output() -> dict:
    return {
        "skill_yaml": (
            "capability_name: parse_task\n"
            "version: 1\n"
            "description: Parse a task.\n"
            "inputs:\n"
            "  schema: {type: object}\n"
            "steps:\n"
            "  - name: extract\n"
            "    kind: llm\n"
            "    prompt: steps/extract.md\n"
            "    output_schema: schemas/extract_v1.json\n"
            "final_output: \"{{ state.extract }}\"\n"
        ),
        "step_prompts": {"extract": "Extract structured fields from the task."},
        "output_schemas": {
            "extract": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
            }
        },
        "fixtures": [
            {
                "case_name": "simple_email",
                "input": {"text": "Buy milk"},
                "expected_output_shape": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            },
            {
                "case_name": "meeting",
                "input": {"text": "Meeting with Dana at 3pm"},
                "expected_output_shape": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            },
            {
                "case_name": "long_task",
                "input": {"text": "Plan the next quarter's OKRs"},
                "expected_output_shape": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            },
        ],
    }


def _make_router(output: dict | Exception) -> MagicMock:
    router = MagicMock()
    if isinstance(output, Exception):
        router.complete = AsyncMock(side_effect=output)
    else:
        router.complete = AsyncMock(return_value=(output, MagicMock()))
    return router


def _stub_passing_executor_factory():
    """Default factory for tests that don't care about validation details.

    Produces an executor whose ``execute`` always returns a ``succeeded``
    run — enough for ``validate_against_fixtures`` to compute a
    ``pass_rate=1.0`` without touching a real LLM.
    """
    executor = MagicMock()
    succeeded = MagicMock()
    succeeded.status = "succeeded"
    succeeded.final_output = {}
    succeeded.error = None
    succeeded.escalation_reason = None
    executor.execute = AsyncMock(return_value=succeeded)
    return executor


def _make_drafter(
    db: aiosqlite.Connection,
    router: MagicMock,
    *,
    executor_factory=None,
    config: SkillSystemConfig | None = None,
    estimated_draft_cost_usd: float = 0.50,
) -> AutoDrafter:
    budget_guard = AsyncMock()
    repo = SkillCandidateRepository(db)
    cfg = config or SkillSystemConfig()
    lifecycle = SkillLifecycleManager(db, config=cfg)
    factory = executor_factory if executor_factory is not None else _stub_passing_executor_factory
    return AutoDrafter(
        connection=db,
        model_router=router,
        budget_guard=budget_guard,
        candidate_repo=repo,
        lifecycle_manager=lifecycle,
        config=cfg,
        executor_factory=factory,
        estimated_draft_cost_usd=estimated_draft_cost_usd,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_successful_draft(db: aiosqlite.Connection) -> None:
    """Candidate → router produces well-formed output → validation passes
    (stub executor returns succeeded) → outcome='drafted'."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    candidate_id = await _insert_candidate(repo, "parse_task")

    router = _make_router(_well_formed_output())
    drafter = _make_drafter(db, router)

    reports = await drafter.run(remaining_budget_usd=5.0, max_drafts=1)

    assert len(reports) == 1
    r = reports[0]
    assert r.outcome == "drafted"
    assert r.skill_id is not None
    assert r.candidate_id == candidate_id
    assert r.pass_rate == 1.0  # stub executor always-succeeds → pass_rate 1.0

    # Candidate marked drafted.
    cursor = await db.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?", (candidate_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "drafted"

    # Skill row exists in 'draft' state.
    cursor = await db.execute(
        "SELECT state, capability_name, current_version_id FROM skill WHERE id = ?",
        (r.skill_id,),
    )
    srow = await cursor.fetchone()
    assert srow[0] == "draft"
    assert srow[1] == "parse_task"
    assert srow[2] is not None

    # Lifecycle audit rows present. The claude_native → skill_candidate → draft
    # path requires two transitions per the spec.
    cursor = await db.execute(
        "SELECT from_state, to_state, reason, actor FROM skill_state_transition WHERE skill_id = ? ORDER BY at",
        (r.skill_id,),
    )
    trows = await cursor.fetchall()
    assert len(trows) == 2
    assert trows[0] == ("claude_native", "skill_candidate", "gate_passed", "system")
    assert trows[1] == ("skill_candidate", "draft", "gate_passed", "system")


async def test_run_budget_exhausted_halts_early(db: aiosqlite.Connection) -> None:
    """remaining_budget_usd < estimated_cost → no drafts attempted."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    await _insert_candidate(repo, "parse_task")

    router = _make_router(_well_formed_output())
    drafter = _make_drafter(db, router, estimated_draft_cost_usd=0.50)

    reports = await drafter.run(remaining_budget_usd=0.1, max_drafts=5)

    assert reports == []
    router.complete.assert_not_called()


async def test_run_budget_paused_mid_stream(db: aiosqlite.Connection) -> None:
    """router.complete raises BudgetPausedError → outcome='budget_exhausted'.
    Candidate remains in status='new'."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    candidate_id = await _insert_candidate(repo, "parse_task")

    router = _make_router(BudgetPausedError(daily_spent=21.0, daily_limit=20.0))
    drafter = _make_drafter(db, router)

    reports = await drafter.run(remaining_budget_usd=5.0, max_drafts=1)

    assert len(reports) == 1
    assert reports[0].outcome == "budget_exhausted"
    assert reports[0].candidate_id == candidate_id

    # Candidate should still be in 'new' status.
    cursor = await db.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?", (candidate_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "new"


async def test_run_validation_failure_dismisses(db: aiosqlite.Connection) -> None:
    """executor_factory produces mock executor with pass_rate=0.6 < 0.8 threshold
    → outcome='dismissed', candidate dismissed, no skill created."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    candidate_id = await _insert_candidate(repo, "parse_task")

    # Mock executor whose validate_against_fixtures will produce 0.6 pass rate.
    # Instead of calling validate_against_fixtures directly, we patch the
    # AutoDrafter's injected validation function via executor_factory producing
    # an executor that consistently fails 40% of fixtures.
    failing_result = MagicMock()
    failing_result.status = "failed"
    failing_result.error = "schema mismatch"
    failing_result.escalation_reason = None

    passing_result = MagicMock()
    passing_result.status = "succeeded"
    passing_result.final_output = {"title": "x"}

    # Three fixtures in well-formed output; first two fail, last one passes → 1/3 = 0.33
    # (below 0.8 threshold regardless; let's make exactly 2 pass / 5 fixtures).
    # Easier: build an output with 5 fixtures and have the mock alternate.
    output = _well_formed_output()
    output["fixtures"] = output["fixtures"] + [
        {
            "case_name": "case4",
            "input": {"text": "x"},
            "expected_output_shape": None,
        },
        {
            "case_name": "case5",
            "input": {"text": "y"},
            "expected_output_shape": None,
        },
    ]

    executor = MagicMock()
    # 3 fail, 2 pass → 2/5 = 0.4
    executor.execute = AsyncMock(
        side_effect=[failing_result, failing_result, failing_result, passing_result, passing_result]
    )

    router = _make_router(output)

    def factory():
        return executor

    drafter = _make_drafter(db, router, executor_factory=factory)

    reports = await drafter.run(remaining_budget_usd=5.0, max_drafts=1)
    assert len(reports) == 1
    r = reports[0]
    assert r.outcome == "dismissed"
    assert r.pass_rate is not None and r.pass_rate < 0.8

    # No skill row.
    cursor = await db.execute("SELECT COUNT(*) FROM skill")
    count = (await cursor.fetchone())[0]
    assert count == 0

    # Candidate dismissed.
    cursor = await db.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?", (candidate_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "dismissed"


async def test_run_malformed_output_dismisses(db: aiosqlite.Connection) -> None:
    """router returns {'foo': 'bar'} — missing required keys → outcome='malformed_output'."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    candidate_id = await _insert_candidate(repo, "parse_task")

    router = _make_router({"foo": "bar"})
    drafter = _make_drafter(db, router)

    reports = await drafter.run(remaining_budget_usd=5.0, max_drafts=1)
    assert len(reports) == 1
    assert reports[0].outcome == "malformed_output"

    cursor = await db.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?", (candidate_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "dismissed"


async def test_draft_one_returns_outcome_for_missing_capability(
    db: aiosqlite.Connection,
) -> None:
    """Candidate references a capability that does not exist → outcome='dismissed'."""
    repo = SkillCandidateRepository(db)
    # Do NOT insert capability row.
    candidate_id = await _insert_candidate(repo, "ghost_capability")

    router = _make_router(_well_formed_output())
    drafter = _make_drafter(db, router)

    candidates = await repo.list_new()
    assert len(candidates) == 1

    report = await drafter.draft_one(candidates[0])
    assert report.outcome == "dismissed"
    assert report.candidate_id == candidate_id

    # Router should NOT have been called for a missing capability.
    router.complete.assert_not_called()

    cursor = await db.execute(
        "SELECT status FROM skill_candidate_report WHERE id = ?", (candidate_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == "dismissed"


async def test_run_max_drafts_respected(db: aiosqlite.Connection) -> None:
    """10 candidates, max_drafts=3 → only 3 attempted."""
    repo = SkillCandidateRepository(db)
    for i in range(10):
        cap_name = f"cap_{i}"
        await _insert_capability(db, cap_name)
        await _insert_candidate(repo, cap_name, savings=20.0 - i)

    router = _make_router(_well_formed_output())
    drafter = _make_drafter(db, router)

    reports = await drafter.run(remaining_budget_usd=10.0, max_drafts=3)
    assert len(reports) == 3
    assert router.complete.await_count == 3


async def test_persisted_skill_has_correct_fields(db: aiosqlite.Connection) -> None:
    """After successful draft, SELECT skill + skill_version verifying all fields."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    await _insert_candidate(repo, "parse_task")

    output = _well_formed_output()
    router = _make_router(output)
    drafter = _make_drafter(db, router)

    reports = await drafter.run(remaining_budget_usd=5.0, max_drafts=1)
    assert reports[0].outcome == "drafted"
    skill_id = reports[0].skill_id
    assert skill_id is not None

    cursor = await db.execute(
        "SELECT capability_name, state, current_version_id FROM skill WHERE id = ?",
        (skill_id,),
    )
    s = await cursor.fetchone()
    assert s[0] == "parse_task"
    assert s[1] == "draft"
    version_id = s[2]
    assert version_id is not None

    cursor = await db.execute(
        """
        SELECT skill_id, version_number, yaml_backbone, step_content,
               output_schemas, created_by, changelog
          FROM skill_version
         WHERE id = ?
        """,
        (version_id,),
    )
    v = await cursor.fetchone()
    assert v[0] == skill_id
    assert v[1] == 1
    assert v[2] == output["skill_yaml"]
    assert json.loads(v[3]) == output["step_prompts"]
    assert json.loads(v[4]) == output["output_schemas"]
    assert v[5] == "claude_auto_draft"
    assert v[6] is not None  # changelog populated


async def test_draft_persists_fixtures_with_tool_mocks(
    db: aiosqlite.Connection,
) -> None:
    """Claude-generated fixtures (including tool_mocks) are persisted to
    ``skill_fixture`` with source='claude_generated'."""
    await _insert_capability(db, "parse_task")
    repo = SkillCandidateRepository(db)
    await _insert_candidate(repo, "parse_task")

    output = _well_formed_output()
    # Stamp a tool_mocks blob onto the first fixture; leave others bare.
    mocks = {'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"}}
    output["fixtures"][0]["tool_mocks"] = mocks

    router = _make_router(output)
    drafter = _make_drafter(db, router)

    reports = await drafter.run(remaining_budget_usd=5.0, max_drafts=1)
    assert reports[0].outcome == "drafted"
    skill_id = reports[0].skill_id

    cursor = await db.execute(
        "SELECT case_name, source, tool_mocks FROM skill_fixture "
        "WHERE skill_id = ? ORDER BY case_name",
        (skill_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 3  # three well-formed fixtures
    for case_name, source, _ in rows:
        assert source == "claude_generated"

    # Find the fixture with the mocks we stamped on.
    matching = [r for r in rows if r[0] == "simple_email"]
    assert matching, "simple_email fixture not persisted"
    assert json.loads(matching[0][2]) == mocks

    # The fixtures without tool_mocks should have NULL.
    bare = [r for r in rows if r[0] != "simple_email"]
    assert bare
    for _, _, tm in bare:
        assert tm is None
