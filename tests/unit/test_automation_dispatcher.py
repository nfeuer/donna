from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.automations.alert import AlertEvaluator
from donna.automations.cron import CronScheduleCalculator
from donna.automations.dispatcher import (
    AutomationDispatcher,
)
from donna.automations.repository import AutomationRepository
from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError


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
        CREATE TABLE automation (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT, capability_name TEXT NOT NULL,
            inputs TEXT NOT NULL, trigger_type TEXT NOT NULL,
            schedule TEXT, alert_conditions TEXT NOT NULL,
            alert_channels TEXT NOT NULL, max_cost_per_run_usd REAL,
            min_interval_seconds INTEGER NOT NULL,
            status TEXT NOT NULL, last_run_at TEXT, next_run_at TEXT,
            run_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_via TEXT NOT NULL,
            active_cadence_cron TEXT
        );
        CREATE TABLE automation_run (
            id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, execution_path TEXT NOT NULL,
            skill_run_id TEXT, invocation_log_id TEXT,
            output TEXT, alert_sent INTEGER NOT NULL DEFAULT 0,
            alert_content TEXT, error TEXT, cost_usd REAL
        );
    """)
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'product_watch', 'cap', '{\"type\":\"object\"}', 'on_schedule', 'active', ?, 'seed')",
        (now,),
    )
    await conn.commit()
    yield conn
    await conn.close()


async def _seed_automation(db, *, alert_conditions=None, max_cost=None):
    repo = AutomationRepository(db)
    auto_id = await repo.create(
        user_id="nick", name="watch shirt", description=None,
        capability_name="product_watch",
        inputs={"url": "https://cos.com/shirt"},
        trigger_type="on_schedule", schedule="0 12 * * *",
        alert_conditions=alert_conditions or {},
        alert_channels=["discord"],
        max_cost_per_run_usd=max_cost,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    return auto_id, await repo.get(auto_id)


def _make_dispatcher(db, **overrides):
    repo = AutomationRepository(db)

    class _ReasonerOutputMeta:
        invocation_id = "inv-reasoner"
        cost_usd = 0.02

    _default_router = object()
    router = overrides.pop("router", _default_router)
    if router is _default_router:
        router = AsyncMock()
        router.complete = AsyncMock(return_value=({"price_usd": 89, "in_stock": True}, _ReasonerOutputMeta()))
    elif not hasattr(router, "complete") or not callable(router.complete) or not getattr(router.complete, "return_value", None):
        router.complete = AsyncMock(return_value=({"price_usd": 89, "in_stock": True}, _ReasonerOutputMeta()))

    budget_guard = overrides.pop("budget_guard", AsyncMock())
    if not hasattr(budget_guard, "check_pre_call") or not callable(budget_guard.check_pre_call):
        budget_guard.check_pre_call = AsyncMock()

    notifier = overrides.pop("notifier", AsyncMock())
    notifier.dispatch = AsyncMock(return_value=True)

    kwargs = dict(
        connection=db,
        repository=repo,
        model_router=router,
        skill_executor_factory=overrides.pop("skill_executor_factory", lambda: None),
        budget_guard=budget_guard,
        alert_evaluator=AlertEvaluator(),
        cron=CronScheduleCalculator(),
        notifier=notifier,
        config=SkillSystemConfig(),
    )
    kwargs.update(overrides)
    return AutomationDispatcher(**kwargs), repo, router, budget_guard, notifier


async def test_claude_native_succeeds_and_advances_schedule(db):
    _, auto = await _seed_automation(
        db, alert_conditions={"all_of": [{"field": "price_usd", "op": "<=", "value": 100}]},
    )
    dispatcher, repo, _router, _budget_guard, notifier = _make_dispatcher(db)

    report = await dispatcher.dispatch(auto)

    assert report.outcome == "succeeded"
    assert report.alert_sent is True
    updated = await repo.get(auto.id)
    assert updated.run_count == 1
    assert updated.failure_count == 0
    assert updated.next_run_at is not None
    runs = await repo.list_runs(auto.id)
    assert len(runs) == 1
    assert runs[0].execution_path == "claude_native"
    assert runs[0].output == {"price_usd": 89, "in_stock": True}
    notifier.dispatch.assert_awaited_once()


async def test_skill_path_is_used_when_skill_is_trusted(db):
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES ('s1', 'product_watch', 'v1', 'trusted', 0, 0.95, ?, ?)",
        (now, now),
    )
    await db.execute(
        "CREATE TABLE skill_version (id TEXT PRIMARY KEY, skill_id TEXT, "
        "version_number INTEGER, yaml_backbone TEXT, step_content TEXT, "
        "output_schemas TEXT, created_by TEXT, changelog TEXT, created_at TEXT)",
    )
    await db.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) VALUES "
        "('v1', 's1', 1, 'x', '{}', '{}', 'seed', NULL, ?)",
        (now,),
    )
    await db.commit()

    _, auto = await _seed_automation(db)

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        status="succeeded", final_output={"price_usd": 42},
        invocation_ids=["inv-skill"],
        total_cost_usd=0.0,
        run_id=None,
    ))
    dispatcher, repo, *_ = _make_dispatcher(
        db, skill_executor_factory=lambda: executor,
    )
    report = await dispatcher.dispatch(auto)
    assert report.outcome == "succeeded"
    runs = await repo.list_runs(auto.id)
    assert runs[0].execution_path == "skill"


async def test_budget_paused_produces_skipped_budget(db):
    _, auto = await _seed_automation(db)
    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock(
        side_effect=BudgetPausedError(daily_spent=30.0, daily_limit=20.0),
    )
    dispatcher, repo, *_ = _make_dispatcher(db, budget_guard=budget_guard)

    report = await dispatcher.dispatch(auto)
    assert report.outcome == "skipped_budget"
    runs = await repo.list_runs(auto.id)
    assert runs == []
    updated = await repo.get(auto.id)
    assert updated.next_run_at is not None


async def test_cost_exceeded_marks_failed(db):
    _, auto = await _seed_automation(db, max_cost=0.01)

    class _BigCostMeta:
        invocation_id = "inv-x"
        cost_usd = 0.50

    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"price_usd": 100}, _BigCostMeta()))
    dispatcher, repo, *_ = _make_dispatcher(db, router=router)
    report = await dispatcher.dispatch(auto)
    assert report.outcome == "cost_exceeded"
    runs = await repo.list_runs(auto.id)
    assert runs[0].status == "failed"
    assert runs[0].error == "cost_exceeded"
    updated = await repo.get(auto.id)
    assert updated.failure_count == 1


async def test_execution_error_marks_failed(db):
    _, auto = await _seed_automation(db)
    router = AsyncMock()
    router.complete = AsyncMock(side_effect=RuntimeError("network broke"))
    dispatcher, repo, *_ = _make_dispatcher(db, router=router)
    report = await dispatcher.dispatch(auto)
    assert report.outcome in ("failed", "error")
    runs = await repo.list_runs(auto.id)
    assert runs[0].status == "failed"
    updated = await repo.get(auto.id)
    assert updated.failure_count == 1


async def test_success_resets_failure_count(db):
    _, auto = await _seed_automation(db)
    repo = AutomationRepository(db)
    await repo.advance_schedule(
        automation_id=auto.id, last_run_at=datetime.now(UTC),
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        increment_run_count=True, increment_failure_count=True,
    )
    await repo.advance_schedule(
        automation_id=auto.id, last_run_at=datetime.now(UTC),
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        increment_run_count=True, increment_failure_count=True,
    )
    auto = await repo.get(auto.id)
    assert auto.failure_count == 2

    dispatcher, _, *_ = _make_dispatcher(db)
    await dispatcher.dispatch(auto)
    updated = await repo.get(auto.id)
    assert updated.failure_count == 0


async def test_consecutive_failures_pause_automation(db):
    _, auto = await _seed_automation(db)
    repo = AutomationRepository(db)
    threshold = SkillSystemConfig().automation_failure_pause_threshold

    await repo.update_fields(auto.id, failure_count=threshold - 1)
    auto = await repo.get(auto.id)
    assert auto.failure_count == threshold - 1

    router = AsyncMock()
    router.complete = AsyncMock(side_effect=RuntimeError("broken"))
    dispatcher, *_, notifier = _make_dispatcher(db, router=router)
    await dispatcher.dispatch(auto)

    updated = await repo.get(auto.id)
    assert updated.status == "paused"
    assert notifier.dispatch.await_count >= 1


async def test_alert_not_sent_when_conditions_false(db):
    _, auto = await _seed_automation(
        db, alert_conditions={"all_of": [{"field": "price_usd", "op": "<=", "value": 10}]},
    )
    dispatcher, _repo, *_, notifier = _make_dispatcher(db)
    notifier.dispatch.reset_mock()
    report = await dispatcher.dispatch(auto)
    assert report.alert_sent is False
    notifier.dispatch.assert_not_awaited()
