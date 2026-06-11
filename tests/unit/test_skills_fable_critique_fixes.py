"""Tests for the Fable-critique safety-critical fixes.

Covers the dispositions implemented from
``docs/superpowers/specs/2026-06-11-skill-system-fable-critique-design.md``:

- #2  human gate scoped to promotions (demotions allowed for system)
- #3  gate evidence version-scoped; baseline_agreement reset on version swap
- #5  contextlib.suppress dead-hop removed → parked-in-draft alert;
      human_approval reason requires a non-system actor
- #6  sandbox gate rejects runs with degraded step outcomes; shadow→trusted
      failure-rate ceiling
- #7  alerting injected into the skills package
- #10 auto-draft default requires_human_gate flips to 1
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.lifecycle import (
    IllegalTransitionError,
    SkillLifecycleManager,
)
from donna.tasks.db_models import SkillState

# ---------------------------------------------------------------------------
# Schema + helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
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
    CREATE TABLE skill_version (
        id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
        version_number INTEGER NOT NULL, yaml_backbone TEXT NOT NULL,
        step_content TEXT NOT NULL, output_schemas TEXT NOT NULL,
        created_by TEXT NOT NULL, changelog TEXT, created_at TEXT NOT NULL
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
        skill_id TEXT NOT NULL,
        skill_version_id TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL
    );
    CREATE TABLE skill_step_result (
        id TEXT PRIMARY KEY,
        skill_run_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        created_at TEXT NOT NULL
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
    conn = await aiosqlite.connect(str(tmp_path / "fable.db"))
    await conn.executescript(_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


async def _insert_skill(
    db: aiosqlite.Connection,
    *,
    skill_id: str = "s1",
    state: str = "sandbox",
    requires_human_gate: bool = False,
    baseline_agreement: float | None = None,
    current_version_id: str | None = "v1",
) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (skill_id, f"cap-{skill_id}", current_version_id, state,
         1 if requires_human_gate else 0, baseline_agreement, now, now),
    )
    await db.commit()


async def _insert_run(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    skill_id: str = "s1",
    skill_version_id: str = "v1",
    status: str = "succeeded",
    degraded_step: bool = False,
) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_run (id, skill_id, skill_version_id, status, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, skill_id, skill_version_id, status, now),
    )
    if degraded_step:
        await db.execute(
            "INSERT INTO skill_step_result (id, skill_run_id, step_name, "
            "validation_status, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"step-{run_id}", run_id, "step0", "continued", now),
        )
    await db.commit()


async def _insert_divergence(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    agreement: float,
) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill_divergence (id, skill_run_id, shadow_invocation_id, "
        "overall_agreement, created_at) VALUES (?, ?, ?, ?, ?)",
        (f"div-{run_id}", run_id, f"inv-{run_id}", agreement, now),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# #5 — human_approval reason requires a non-system actor
# ---------------------------------------------------------------------------


async def test_human_approval_reason_rejects_system_actor(db: aiosqlite.Connection) -> None:
    """A system actor may not use reason='human_approval' (Fable #5)."""
    await _insert_skill(db, state="draft")
    mgr = SkillLifecycleManager(db, SkillSystemConfig())
    with pytest.raises(IllegalTransitionError, match="human_approval"):
        await mgr.transition(
            "s1", SkillState.SANDBOX, reason="human_approval", actor="system",
        )


async def test_human_approval_reason_allows_user_actor(db: aiosqlite.Connection) -> None:
    """A user actor may use reason='human_approval'."""
    await _insert_skill(db, state="draft")
    mgr = SkillLifecycleManager(db, SkillSystemConfig())
    await mgr.transition(
        "s1", SkillState.SANDBOX, reason="human_approval", actor="user",
        actor_id="nick",
    )
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "sandbox"


# ---------------------------------------------------------------------------
# #3 — version-scoped gate evidence
# ---------------------------------------------------------------------------


async def test_sandbox_gate_ignores_predecessor_version_runs(
    db: aiosqlite.Connection,
) -> None:
    """Runs from an older version must not count toward the current version's gate."""
    await _insert_skill(db, state="sandbox", current_version_id="v2")
    # 20 clean runs, but all under the *previous* version v1.
    for i in range(20):
        await _insert_run(db, run_id=f"old-{i}", skill_version_id="v1")

    cfg = SkillSystemConfig(
        sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9,
    )
    mgr = SkillLifecycleManager(db, cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "sandbox"


async def test_sandbox_gate_counts_current_version_runs(
    db: aiosqlite.Connection,
) -> None:
    """Runs under the current version DO count toward the gate."""
    await _insert_skill(db, state="sandbox", current_version_id="v2")
    for i in range(20):
        await _insert_run(db, run_id=f"new-{i}", skill_version_id="v2")

    cfg = SkillSystemConfig(
        sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9,
    )
    mgr = SkillLifecycleManager(db, cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result == "shadow_primary"


async def test_sandbox_gate_no_promotion_when_version_id_null(
    db: aiosqlite.Connection,
) -> None:
    """A skill with no current_version_id cannot be promoted (fail closed)."""
    await _insert_skill(db, state="sandbox", current_version_id=None)
    for i in range(20):
        await _insert_run(db, run_id=f"r-{i}", skill_version_id="v1")

    cfg = SkillSystemConfig(
        sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9,
    )
    mgr = SkillLifecycleManager(db, cfg)
    assert await mgr.check_and_promote_if_eligible("s1") is None


# ---------------------------------------------------------------------------
# #6 — sandbox gate rejects runs with degraded step outcomes
# ---------------------------------------------------------------------------


async def test_sandbox_gate_rejects_continued_steps(db: aiosqlite.Connection) -> None:
    """A 'succeeded' run with a 'continued' step is NOT valid evidence (Fable #6)."""
    await _insert_skill(db, state="sandbox", current_version_id="v1")
    # All runs report status='succeeded', but each has a continued (degraded) step.
    for i in range(20):
        await _insert_run(db, run_id=f"r-{i}", status="succeeded", degraded_step=True)

    cfg = SkillSystemConfig(
        sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9,
    )
    mgr = SkillLifecycleManager(db, cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "sandbox"


async def test_sandbox_gate_passes_clean_succeeded_runs(db: aiosqlite.Connection) -> None:
    """Succeeded runs with no degraded step outcomes promote normally."""
    await _insert_skill(db, state="sandbox", current_version_id="v1")
    for i in range(20):
        await _insert_run(db, run_id=f"r-{i}", status="succeeded", degraded_step=False)

    cfg = SkillSystemConfig(
        sandbox_promotion_min_runs=20, sandbox_promotion_validity_rate=0.9,
    )
    mgr = SkillLifecycleManager(db, cfg)
    assert await mgr.check_and_promote_if_eligible("s1") == "shadow_primary"


# ---------------------------------------------------------------------------
# #6 — shadow→trusted failure-rate ceiling
# ---------------------------------------------------------------------------


async def test_trusted_gate_blocked_by_failure_rate_ceiling(
    db: aiosqlite.Connection,
) -> None:
    """High agreement must not promote a skill that frequently fails (Fable #6)."""
    await _insert_skill(db, state="shadow_primary", current_version_id="v1")
    # 100 divergences all at high agreement, but 30% of the runs failed outright.
    for i in range(100):
        status = "failed" if i < 30 else "succeeded"
        await _insert_run(db, run_id=f"r-{i}", status=status)
        await _insert_divergence(db, run_id=f"r-{i}", agreement=0.95)

    cfg = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
        shadow_primary_promotion_max_failure_rate=0.10,
    )
    mgr = SkillLifecycleManager(db, cfg)
    result = await mgr.check_and_promote_if_eligible("s1")

    assert result is None
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "shadow_primary"


async def test_trusted_gate_passes_within_failure_ceiling(
    db: aiosqlite.Connection,
) -> None:
    """Low failure rate + high agreement promotes to trusted."""
    await _insert_skill(db, state="shadow_primary", current_version_id="v1")
    for i in range(100):
        await _insert_run(db, run_id=f"r-{i}", status="succeeded")
        await _insert_divergence(db, run_id=f"r-{i}", agreement=0.95)

    cfg = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
        shadow_primary_promotion_max_failure_rate=0.10,
    )
    mgr = SkillLifecycleManager(db, cfg)
    assert await mgr.check_and_promote_if_eligible("s1") == "trusted"


async def test_trusted_gate_ignores_predecessor_version_divergences(
    db: aiosqlite.Connection,
) -> None:
    """Divergences from an older version must not count (Fable #3)."""
    await _insert_skill(db, state="shadow_primary", current_version_id="v2")
    for i in range(100):
        await _insert_run(db, run_id=f"r-{i}", skill_version_id="v1", status="succeeded")
        await _insert_divergence(db, run_id=f"r-{i}", agreement=0.95)

    cfg = SkillSystemConfig(
        shadow_primary_promotion_min_runs=100,
        shadow_primary_promotion_agreement_rate=0.85,
    )
    mgr = SkillLifecycleManager(db, cfg)
    assert await mgr.check_and_promote_if_eligible("s1") is None


# ---------------------------------------------------------------------------
# #7 — degradation alerts + per-skill isolation; #2 demotion allowed
# ---------------------------------------------------------------------------


async def test_degradation_flagging_alerts_user(db: aiosqlite.Connection) -> None:
    """When a trusted skill degrades, the user is alerted (Fable #7)."""
    from donna.skills.degradation import DegradationDetector
    from donna.skills.divergence import SkillDivergenceRepository

    # baseline 0.9; 30 divergences at 0.3 → CI upper well below baseline → flag.
    await _insert_skill(db, state="trusted", baseline_agreement=0.9)
    for i in range(30):
        await _insert_run(db, run_id=f"r-{i}", status="succeeded")
        await _insert_divergence(db, run_id=f"r-{i}", agreement=0.3)

    alerts: list[dict] = []

    async def _alert(**kwargs) -> bool:
        alerts.append(kwargs)
        return True

    cfg = SkillSystemConfig(degradation_rolling_window=30)
    lifecycle = SkillLifecycleManager(db, cfg)
    detector = DegradationDetector(
        connection=db,
        divergence_repo=SkillDivergenceRepository(db),
        lifecycle_manager=lifecycle,
        config=cfg,
        fallback_alert=_alert,
    )

    reports = await detector.run()

    assert any(r.outcome == "flagged" for r in reports)
    # The demotion fired despite no human gate involvement, and the user was told.
    assert any(a["component"] == "skill_degradation" for a in alerts)
    cursor = await db.execute("SELECT state FROM skill WHERE id = 's1'")
    assert (await cursor.fetchone())[0] == "flagged_for_review"


async def test_degradation_sweep_continues_past_one_failure(
    db: aiosqlite.Connection,
) -> None:
    """One skill raising must not abort the whole nightly sweep (Fable #2)."""
    from donna.skills.degradation import DegradationDetector
    from donna.skills.divergence import SkillDivergenceRepository

    # Two trusted skills, each with enough degraded divergences to flag.
    for sid in ("s1", "s2"):
        await _insert_skill(db, skill_id=sid, state="trusted", baseline_agreement=0.9)
        for i in range(30):
            await _insert_run(db, run_id=f"{sid}-r-{i}", skill_id=sid, status="succeeded")
            await _insert_divergence(db, run_id=f"{sid}-r-{i}", agreement=0.3)

    alerts: list[dict] = []

    async def _alert(**kwargs) -> bool:
        alerts.append(kwargs)
        return True

    cfg = SkillSystemConfig(degradation_rolling_window=30)
    lifecycle = SkillLifecycleManager(db, cfg)
    detector = DegradationDetector(
        connection=db,
        divergence_repo=SkillDivergenceRepository(db),
        lifecycle_manager=lifecycle,
        config=cfg,
        fallback_alert=_alert,
    )

    # Make the FIRST evaluated skill blow up; the sweep must still process s2.
    original = detector._evaluate_skill
    seen: list[str] = []

    async def _boom(skill_id: str, baseline: float | None):
        seen.append(skill_id)
        if len(seen) == 1:
            raise RuntimeError("boom")
        return await original(skill_id, baseline)

    detector._evaluate_skill = _boom  # type: ignore[method-assign]

    reports = await detector.run()

    # First skill raised (alerted, no report); second was still evaluated.
    assert len(seen) == 2
    assert any(a["component"] == "skill_degradation" for a in alerts)
    assert any(r.outcome == "flagged" for r in reports)


# ---------------------------------------------------------------------------
# #10 — auto-draft default requires_human_gate flips to 1
# ---------------------------------------------------------------------------


async def test_auto_draft_default_requires_human_gate(db: aiosqlite.Connection) -> None:
    """A freshly persisted auto-draft skill must default to requires_human_gate=1."""
    from unittest.mock import AsyncMock, MagicMock

    from donna.skills.auto_drafter import AutoDrafter

    drafter = AutoDrafter(
        connection=db,
        model_router=MagicMock(),
        budget_guard=AsyncMock(),
        candidate_repo=MagicMock(),
        lifecycle_manager=MagicMock(),
        config=SkillSystemConfig(),
        executor_factory=lambda: MagicMock(),
    )
    skill_id = await drafter._persist_draft(
        capability_name="parse_task",
        skill_yaml="capability_name: parse_task\nversion: 1\nsteps: []\n",
        step_prompts={},
        output_schemas={},
    )
    cursor = await db.execute(
        "SELECT requires_human_gate FROM skill WHERE id = ?", (skill_id,),
    )
    assert (await cursor.fetchone())[0] == 1


# ---------------------------------------------------------------------------
# #3 / #5 — evolution resets baseline + parks in draft with an alert
# ---------------------------------------------------------------------------


async def test_persist_new_version_resets_baseline_agreement(
    db: aiosqlite.Connection,
) -> None:
    """Swapping current_version_id must NULL out baseline_agreement (Fable #3)."""
    from unittest.mock import AsyncMock, MagicMock

    from donna.skills.evolution import Evolver

    await _insert_skill(
        db, state="degraded", baseline_agreement=0.9, current_version_id="v1",
    )
    await db.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) "
        "VALUES ('v1', 's1', 1, '', '{}', '{}', 'seed', 'v1', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    await db.commit()

    evolver = Evolver(
        connection=db,
        model_router=MagicMock(),
        budget_guard=AsyncMock(),
        lifecycle_manager=MagicMock(),
        config=SkillSystemConfig(),
        executor_factory=lambda: MagicMock(),
    )

    new_version_id = await evolver._persist_new_version(
        skill_id="s1",
        current_version_id="v1",
        new_version={"yaml_backbone": "", "step_content": {}, "output_schemas": {}},
        changelog="evolved",
    )

    cursor = await db.execute(
        "SELECT current_version_id, baseline_agreement FROM skill WHERE id = 's1'",
    )
    row = await cursor.fetchone()
    assert row[0] == new_version_id
    assert row[1] is None


def test_evolution_no_longer_imports_contextlib() -> None:
    """The contextlib.suppress dead-hop is gone (Fable #5).

    A direct guard against re-introducing the suppressed, always-failing
    draft→sandbox hop: ``evolution.py`` must not import ``contextlib`` anymore,
    and the module must not contain a live ``contextlib.suppress(...)`` call.
    """
    import ast
    import inspect

    import donna.skills.evolution as evo

    # The module no longer imports contextlib at all.
    assert not hasattr(evo, "contextlib")

    # And there is no live contextlib.suppress call in the AST (comments excluded).
    tree = ast.parse(inspect.getsource(evo))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            assert not (
                isinstance(value, ast.Name)
                and value.id == "contextlib"
                and node.func.attr == "suppress"
            )


# ---------------------------------------------------------------------------
# #1 — executor evidence-loop wiring shape
# ---------------------------------------------------------------------------


def test_executor_accepts_run_repository_and_shadow_sampler() -> None:
    """The executor exposes run_repository + shadow_sampler + fallback_alert (Fable #1)."""
    from unittest.mock import MagicMock

    from donna.skills.executor import SkillExecutor

    run_repo = MagicMock()
    sampler = MagicMock()

    async def _alert(**kwargs) -> bool:
        return True

    executor = SkillExecutor(
        model_router=MagicMock(),
        run_repository=run_repo,
        shadow_sampler=sampler,
        fallback_alert=_alert,
    )
    # The evidence loop is wired: a run repository and a shadow sampler are set.
    assert executor._run_repository is run_repo
    assert executor._shadow_sampler is sampler
    assert executor._fallback_alert is _alert


async def test_executor_alerts_on_run_persistence_finish_failure() -> None:
    """A finish_run write failure must alert, not be swallowed (Fable #7)."""
    from unittest.mock import MagicMock

    from donna.skills.executor import SkillExecutor, SkillRunResult

    class _BoomRepo:
        async def finish_run(self, *args, **kwargs):
            raise RuntimeError("disk full")

    alerts: list[dict] = []

    async def _alert(**kwargs) -> bool:
        alerts.append(kwargs)
        return True

    executor = SkillExecutor(
        model_router=MagicMock(),
        run_repository=_BoomRepo(),
        fallback_alert=_alert,
    )
    # Drive the private finish helper directly with a live run id.
    await executor._finish_run_if_repo("run-1", SkillRunResult(status="succeeded"))

    assert len(alerts) == 1
    assert alerts[0]["component"] == "skill_executor"


# ---------------------------------------------------------------------------
# #1 — boot-time evidence-loop invariant
# ---------------------------------------------------------------------------


class _StubCtx:
    """Minimal StartupContext stand-in for the boot invariant check."""

    def __init__(self, conn: aiosqlite.Connection, alert_fn) -> None:
        from types import SimpleNamespace

        self.skill_config = SkillSystemConfig(enabled=True)
        self.db = SimpleNamespace(connection=conn)
        self.notification_service = SimpleNamespace(dispatch_fallback_alert=alert_fn)


async def test_boot_invariant_alerts_when_live_skill_lacks_evidence_loop(
    db: aiosqlite.Connection,
) -> None:
    """A live (shadow_primary) skill with no sampler/repo must trigger an alert (Fable #1)."""
    from donna.cli_wiring import _verify_evidence_loop_wired

    await _insert_skill(db, state="shadow_primary")

    alerts: list[dict] = []

    async def _alert(**kwargs) -> bool:
        alerts.append(kwargs)
        return True

    await _verify_evidence_loop_wired(
        ctx=_StubCtx(db, _alert),
        run_repository=None,
        shadow_sampler=None,
    )

    assert len(alerts) == 1
    assert alerts[0]["component"] == "skill_executor_wiring"
    assert "run_repository" in alerts[0]["context"]["missing"]
    assert "shadow_sampler" in alerts[0]["context"]["missing"]


async def test_boot_invariant_silent_when_wired(db: aiosqlite.Connection) -> None:
    """No alert when run persistence + sampler are both present."""
    from unittest.mock import MagicMock

    from donna.cli_wiring import _verify_evidence_loop_wired

    await _insert_skill(db, state="trusted")

    alerts: list[dict] = []

    async def _alert(**kwargs) -> bool:
        alerts.append(kwargs)
        return True

    await _verify_evidence_loop_wired(
        ctx=_StubCtx(db, _alert),
        run_repository=MagicMock(),
        shadow_sampler=MagicMock(),
    )

    assert alerts == []


async def test_boot_invariant_silent_when_no_live_skills(
    db: aiosqlite.Connection,
) -> None:
    """No alert when no skill is live, even if the loop is unwired."""
    from donna.cli_wiring import _verify_evidence_loop_wired

    await _insert_skill(db, state="sandbox")  # not live

    alerts: list[dict] = []

    async def _alert(**kwargs) -> bool:
        alerts.append(kwargs)
        return True

    await _verify_evidence_loop_wired(
        ctx=_StubCtx(db, _alert),
        run_repository=None,
        shadow_sampler=None,
    )

    assert alerts == []
