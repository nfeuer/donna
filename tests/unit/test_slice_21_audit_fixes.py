"""Regression tests for slice 21 self-review fixes.

These tests pin behaviors that were missing or wrong in the initial
slice 21 commit and got fixed in the follow-up:

1. Router raises ``EscalationDecisionError`` on ``claude_code`` and
   ``chat`` modes (not just pause/cancel) — falling through would
   charge the budget for an API call the user is replacing.
2. AutoDrafter / Evolver catch the new modes and report
   ``manual_handoff_pending`` instead of dismissing the candidate or
   surfacing a generic error.
3. EscalationGate.fire_and_wait de-dup only re-delivers Discord
   notifications when the existing row is in ``open`` state — re-
   delivering on resolved/submitted/failed would spam the user about
   work already in flight.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    BudgetExtensionConfig,
    ClaudeCodeModeConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tracker import CostSummary, CostTracker
from donna.models.router import EscalationDecisionError

_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT,
    parent_escalation_id INTEGER,
    human_review INTEGER NOT NULL DEFAULT 0,
    target_paths TEXT,
    originating_entity_type TEXT,
    originating_entity_id TEXT,
    base_sha TEXT,
    merged_at TEXT
);
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER
);
CREATE TABLE daily_budget_extension (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    escalation_request_id INTEGER,
    voided INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "auditfix.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


def _config() -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(),
        budget_extension=BudgetExtensionConfig(),
    )


def _stub_extension_repo() -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=0.0)
    stub.get_monthly_total = AsyncMock(return_value=0.0)
    return stub


# ---------------------------------------------------------------------------
# 1. Router raises EscalationDecisionError on claude_code / chat
# ---------------------------------------------------------------------------


def _make_router_with_gate(gate: Any) -> Any:
    """Build a minimal ModelRouter just for exercising the
    estimate-bearing gate branch in ``complete()``."""
    from donna.config import ModelsConfig, TaskTypesConfig
    from donna.models.router import ModelRouter

    cfg = ModelsConfig(models={}, routing={})
    task_types = TaskTypesConfig(task_types={})
    return ModelRouter(
        models_config=cfg,
        task_types_config=task_types,
        project_root=Path("."),
        escalation_gate=gate,
        invocation_logger=AsyncMock(),
    )


async def test_router_raises_decision_error_on_claude_code() -> None:
    """The router MUST NOT silently fall through to make the API call
    when the gate resolved as claude_code — that would spend budget on
    a request the user is doing manually."""
    from donna.cost.escalation_gate import GateOutcome

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(
        return_value=GateOutcome(
            fired=True, mode="claude_code", resolved_by="user",
            escalation_request_id=42, correlation_id="cc-x",
        )
    )
    router = _make_router_with_gate(gate)
    with pytest.raises(EscalationDecisionError) as exc:
        await router.complete(
            prompt="x",
            task_type="skill_auto_draft",
            estimate_usd=10.0,
            originating_entity=("skill_candidate_report", "cand-1"),
        )
    assert exc.value.mode == "claude_code"
    assert exc.value.escalation_request_id == 42


async def test_router_raises_decision_error_on_chat() -> None:
    from donna.cost.escalation_gate import GateOutcome

    gate = MagicMock()
    gate.fire_and_wait = AsyncMock(
        return_value=GateOutcome(
            fired=True, mode="chat", resolved_by="user",
            escalation_request_id=43, correlation_id="cc-y",
        )
    )
    router = _make_router_with_gate(gate)
    with pytest.raises(EscalationDecisionError) as exc:
        await router.complete(
            prompt="x",
            task_type="chat_escalation",
            estimate_usd=10.0,
        )
    assert exc.value.mode == "chat"


# ---------------------------------------------------------------------------
# 2. De-dup behavior
# ---------------------------------------------------------------------------


async def _seed_existing_claude_code(
    conn: aiosqlite.Connection,
    *,
    correlation_id: str,
    status: str,
) -> None:
    """Insert an existing claude_code escalation in the given status."""
    await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_type, estimate_usd, daily_remaining_usd,
            offered_modes, mode, status, iteration, created_at, priority,
            originating_entity_type, originating_entity_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "nick", correlation_id, "skill_auto_draft", 6.0, 1.0,
            json.dumps(["claude_code", "pause", "cancel"]),
            "claude_code", status, 1,
            "2026-05-06T12:00:00+00:00", 2,
            "skill_candidate_report", "cand-1",
        ),
    )
    await conn.commit()


def _gate(repo: EscalationRepository, deliver: AsyncMock) -> EscalationGate:
    tracker = MagicMock(spec=CostTracker)
    tracker.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
    )
    return EscalationGate(
        repository=repo,
        tracker=tracker,
        config=_config(),
        daily_pause_threshold_usd=20.0,
        resolver=DashboardSettingResolver(repo),
        deliver=deliver,
        extension_repo=_stub_extension_repo(),
    )


async def test_dedup_open_row_redelivers_discord_ping(
    repo: EscalationRepository,
    conn: aiosqlite.Connection,
) -> None:
    """An existing OPEN row → re-deliver the Discord ping (the user
    may have missed it)."""
    await _seed_existing_claude_code(conn, correlation_id="cc-1", status="open")
    deliver = AsyncMock(return_value=True)
    gate = _gate(repo, deliver)
    outcome = await gate.fire_and_wait(
        user_id="nick",
        task_id=None,
        task_type="skill_auto_draft",
        estimate_usd=10.0,
        originating_entity=("skill_candidate_report", "cand-1"),
    )
    assert outcome.fired is False
    deliver.assert_called_once()


@pytest.mark.parametrize("status", ["resolved", "submitted", "failed"])
async def test_dedup_in_flight_row_does_not_redeliver(
    repo: EscalationRepository,
    conn: aiosqlite.Connection,
    status: str,
) -> None:
    """An in-flight row (resolved/submitted/failed) → DON'T re-deliver.
    The user already knows about the work and is doing / waiting on it.
    Re-pinging would spam them about something they're already on."""
    await _seed_existing_claude_code(
        conn, correlation_id=f"cc-{status}", status=status
    )
    deliver = AsyncMock(return_value=True)
    gate = _gate(repo, deliver)
    outcome = await gate.fire_and_wait(
        user_id="nick",
        task_id=None,
        task_type="skill_auto_draft",
        estimate_usd=10.0,
        originating_entity=("skill_candidate_report", "cand-1"),
    )
    assert outcome.fired is False
    deliver.assert_not_called()


# ---------------------------------------------------------------------------
# 3. AutoDrafter / Evolver report manual_handoff_pending
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. ManualValidationRouter._promote_to_sandbox handles each starting state
# ---------------------------------------------------------------------------


@pytest.fixture
async def lifecycle_conn(tmp_path: Path):
    """Tiny aiosqlite connection with the skill / skill_state_transition
    tables that SkillLifecycleManager writes to."""
    schema = """
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
"""
    c = await aiosqlite.connect(str(tmp_path / "lifecycle.db"))
    await c.executescript(schema)
    await c.commit()
    yield c
    await c.close()


async def _seed_skill(
    conn: aiosqlite.Connection, *, skill_id: str, state: str
) -> None:
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, NULL, '2026-05-06', '2026-05-06')",
        (skill_id, f"cap-{skill_id}", None, state),
    )
    await conn.commit()


def _make_router_promoter(conn: aiosqlite.Connection) -> Any:
    from donna.config import SkillSystemConfig
    from donna.cost.escalation_repository import EscalationRequestRow
    from donna.cost.manual_validation_router import ManualValidationRouter
    from donna.skills.lifecycle import SkillLifecycleManager

    # Minimal config — only needs the per-run timeout.
    config = SkillSystemConfig(enabled=True, validation_per_run_timeout_s=10)
    lifecycle = SkillLifecycleManager(conn, config)
    return ManualValidationRouter(
        conn=conn,
        host_repo=MagicMock(),
        executor_factory=lambda: MagicMock(),
        lifecycle=lifecycle,
    ), EscalationRequestRow


def _make_row(rowtype: Any, *, correlation_id: str = "cc-x") -> Any:
    from datetime import UTC
    from datetime import datetime as _dt
    return rowtype(
        id=1, user_id="nick", correlation_id=correlation_id,
        task_id=None, task_type="skill_evolution",
        estimate_usd=10.0, daily_remaining_usd=1.0,
        offered_modes=["claude_code"], resolution=None, resolved_by=None,
        resolved_at=None, iteration=1, status="submitted",
        created_at=_dt.now(tz=UTC), priority=2,
        delivery_status=None, delivery_attempts=0,
        last_delivery_attempt_at=None,
    )


async def test_promote_to_sandbox_from_claude_native(
    lifecycle_conn: aiosqlite.Connection,
) -> None:
    await _seed_skill(lifecycle_conn, skill_id="s1", state="claude_native")
    router, row_cls = _make_router_promoter(lifecycle_conn)
    await router._promote_to_sandbox(
        skill_id="s1", row=_make_row(row_cls), actor_id="999"
    )
    cur = await lifecycle_conn.execute(
        "SELECT state FROM skill WHERE id = 's1'"
    )
    assert (await cur.fetchone())[0] == "sandbox"


async def test_promote_to_sandbox_from_degraded(
    lifecycle_conn: aiosqlite.Connection,
) -> None:
    """skill_evolution path — existing skill in degraded state must
    transition through draft to sandbox without IllegalTransitionError."""
    await _seed_skill(lifecycle_conn, skill_id="s2", state="degraded")
    router, row_cls = _make_router_promoter(lifecycle_conn)
    await router._promote_to_sandbox(
        skill_id="s2", row=_make_row(row_cls), actor_id="999"
    )
    cur = await lifecycle_conn.execute(
        "SELECT state FROM skill WHERE id = 's2'"
    )
    assert (await cur.fetchone())[0] == "sandbox"


async def test_promote_to_sandbox_from_flagged_for_review(
    lifecycle_conn: aiosqlite.Connection,
) -> None:
    """flagged_for_review skill — manual handoff replaces it via
    flagged_for_review → degraded → draft → sandbox."""
    await _seed_skill(
        lifecycle_conn, skill_id="s3", state="flagged_for_review"
    )
    router, row_cls = _make_router_promoter(lifecycle_conn)
    await router._promote_to_sandbox(
        skill_id="s3", row=_make_row(row_cls), actor_id="999"
    )
    cur = await lifecycle_conn.execute(
        "SELECT state FROM skill WHERE id = 's3'"
    )
    assert (await cur.fetchone())[0] == "sandbox"


async def test_promote_to_sandbox_from_draft(
    lifecycle_conn: aiosqlite.Connection,
) -> None:
    """draft → sandbox via the human_approval hop only."""
    await _seed_skill(lifecycle_conn, skill_id="s4", state="draft")
    router, row_cls = _make_router_promoter(lifecycle_conn)
    await router._promote_to_sandbox(
        skill_id="s4", row=_make_row(row_cls), actor_id="999"
    )
    cur = await lifecycle_conn.execute(
        "SELECT state FROM skill WHERE id = 's4'"
    )
    assert (await cur.fetchone())[0] == "sandbox"


async def test_promote_to_sandbox_from_active_state_fails(
    lifecycle_conn: aiosqlite.Connection,
) -> None:
    """Defensive: trying to promote a trusted skill via manual handoff
    is illegal (gate's de-dup should have blocked the new escalation)."""
    from donna.skills.lifecycle import IllegalTransitionError

    await _seed_skill(lifecycle_conn, skill_id="s5", state="trusted")
    router, row_cls = _make_router_promoter(lifecycle_conn)
    with pytest.raises(IllegalTransitionError):
        await router._promote_to_sandbox(
            skill_id="s5", row=_make_row(row_cls), actor_id="999"
        )


async def test_auto_drafter_reports_manual_handoff_pending() -> None:
    """When the gate resolves as claude_code, the candidate must NOT
    be dismissed — the poller will mark it drafted on success."""
    from datetime import UTC, datetime

    from donna.skills.auto_drafter import AutoDrafter, AutoDraftReport
    from donna.skills.candidate_report import (
        SkillCandidateReportRow,
        SkillCandidateRepository,
    )

    candidate = SkillCandidateReportRow(
        id="cand-1",
        capability_name="bookmark",
        task_pattern_hash=None,
        expected_savings_usd=5.0,
        volume_30d=100,
        variance_score=0.5,
        status="new",
        reported_at=datetime.now(tz=UTC),
        resolved_at=None,
    )

    repo = MagicMock(spec=SkillCandidateRepository)
    repo.list_new = AsyncMock(return_value=[candidate])
    repo.mark_dismissed = AsyncMock()
    repo.mark_drafted = AsyncMock()

    router = MagicMock()
    router.complete = AsyncMock(
        side_effect=EscalationDecisionError(
            mode="claude_code",
            escalation_request_id=99,
            correlation_id="cc-9",
        )
    )

    config = MagicMock()
    drafter = AutoDrafter(
        connection=MagicMock(),
        model_router=router,
        budget_guard=None,
        candidate_repo=repo,
        lifecycle_manager=MagicMock(),
        config=config,
        executor_factory=lambda: MagicMock(),
    )
    # Need to stub the capability lookup so we get past the guard.
    drafter._lookup_capability = AsyncMock(
        return_value={"name": "bookmark"}
    )
    drafter._recent_invocation_samples = AsyncMock(return_value=[])

    report = await drafter.draft_one(candidate)
    assert isinstance(report, AutoDraftReport)
    assert report.outcome == "manual_handoff_pending"
    # Crucially, the candidate is NOT marked dismissed — the poller
    # will mark it drafted when validation lands.
    repo.mark_dismissed.assert_not_called()
    repo.mark_drafted.assert_not_called()
