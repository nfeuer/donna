"""Phase 5 end-to-end integration test.

Verifies spec Phase 5 automation acceptance scenarios AS-5.1 through AS-5.5:
  AS-5.1: Dashboard POST creates automation with cron schedule.
  AS-5.2: Scheduler dispatches due automation via claude_native path.
  AS-5.3: Skill path used once skill reaches shadow_primary.
  AS-5.4: Alert fires when condition is true.
  AS-5.5: 5 consecutive failures pause the automation.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest


@pytest.fixture
async def phase5_db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "phase5.db"))
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
            created_via TEXT NOT NULL
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
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'product_watch', 'cap', '{}', 'on_schedule', 'active', ?, 'seed')",
        (now,),
    )
    await conn.commit()
    yield conn
    await conn.close()


def _make_dispatcher(db, *, router=None, notifier=None, skill_factory=None):
    from donna.automations.alert import AlertEvaluator
    from donna.automations.cron import CronScheduleCalculator
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.repository import AutomationRepository
    from donna.config import SkillSystemConfig

    repo = AutomationRepository(db)

    class _DefaultMeta:
        invocation_id = "inv-1"
        cost_usd = 0.02

    if router is None:
        router = AsyncMock()
        router.complete = AsyncMock(return_value=({"price_usd": 150}, _DefaultMeta()))

    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock()

    if notifier is None:
        notifier = AsyncMock()
        notifier.dispatch = AsyncMock(return_value=True)

    return AutomationDispatcher(
        connection=db,
        repository=repo,
        model_router=router,
        skill_executor_factory=skill_factory or (lambda: None),
        budget_guard=budget_guard,
        alert_evaluator=AlertEvaluator(),
        cron=CronScheduleCalculator(),
        notifier=notifier,
        config=SkillSystemConfig(),
    ), repo, router, notifier


async def _seed_automation(db, *, alert_conditions=None, next_run_offset_minutes=-1):
    from donna.automations.repository import AutomationRepository

    repo = AutomationRepository(db)
    return await repo.create(
        user_id="nick",
        name="watch shirt",
        description=None,
        capability_name="product_watch",
        inputs={"url": "https://example.com"},
        trigger_type="on_schedule",
        schedule="0 12 * * *",
        alert_conditions=alert_conditions or {},
        alert_channels=["discord"],
        max_cost_per_run_usd=2.0,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=datetime.now(timezone.utc) + timedelta(minutes=next_run_offset_minutes),
    )


# ---------------------------------------------------------------------------
# AS-5.1: Dashboard POST creates automation with cron schedule.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_as_5_1_create_automation_with_cron_schedule(phase5_db):
    from donna.automations.cron import CronScheduleCalculator
    from donna.automations.repository import AutomationRepository

    cron = CronScheduleCalculator()
    schedule = "0 12 * * *"
    now = datetime.now(timezone.utc)
    expected_next = cron.next_run(expression=schedule, after=now)

    repo = AutomationRepository(phase5_db)
    auto_id = await repo.create(
        user_id="nick",
        name="daily price watch",
        description="watch for price drop",
        capability_name="product_watch",
        inputs={"url": "https://example.com/shirt"},
        trigger_type="on_schedule",
        schedule=schedule,
        alert_conditions={"all_of": [{"field": "price_usd", "op": "<=", "value": 100}]},
        alert_channels=["discord"],
        max_cost_per_run_usd=2.0,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=expected_next,
    )

    auto = await repo.get(auto_id)
    assert auto is not None
    assert auto.status == "active"
    assert auto.run_count == 0
    assert auto.failure_count == 0
    assert auto.next_run_at is not None
    # next_run_at should be within a couple seconds of what we computed
    delta = abs((auto.next_run_at - expected_next).total_seconds())
    assert delta < 2, f"next_run_at drift too large: {delta}s"


# ---------------------------------------------------------------------------
# AS-5.2: Scheduler dispatches due automation via claude_native path.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_as_5_2_dispatch_due_automation_via_claude_native(phase5_db):
    from donna.automations.repository import AutomationRepository

    class _Meta:
        invocation_id = "inv-1"
        cost_usd = 0.02

    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"price_usd": 150}, _Meta()))

    notifier = AsyncMock()
    notifier.dispatch = AsyncMock(return_value=True)

    # Seed an automation with next_run_at in the past (no skill row exists)
    auto_id = await _seed_automation(phase5_db, next_run_offset_minutes=-5)
    repo = AutomationRepository(phase5_db)
    auto = await repo.get(auto_id)

    dispatcher, repo, router, notifier = _make_dispatcher(
        phase5_db, router=router, notifier=notifier
    )

    report = await dispatcher.dispatch(auto)

    assert report.outcome == "succeeded"
    assert report.alert_sent is False  # price_usd=150, condition <=100 is False

    # Check automation_run row
    runs = await repo.list_runs(auto_id)
    assert len(runs) == 1
    run = runs[0]
    assert run.execution_path == "claude_native"
    assert run.status == "succeeded"
    assert run.alert_sent is False

    # Check automation counters and next_run_at advanced
    updated = await repo.get(auto_id)
    assert updated.run_count == 1
    assert updated.failure_count == 0
    assert updated.next_run_at is not None

    # Notifier should NOT have been called (alert condition false)
    notifier.dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# AS-5.3: Skill path used once skill reaches shadow_primary.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_as_5_3_skill_path_when_shadow_primary(phase5_db):
    from donna.automations.repository import AutomationRepository

    now = datetime.now(timezone.utc).isoformat()

    # Seed a skill in shadow_primary state with a valid skill_version
    await phase5_db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES ('sk1', 'product_watch', 'sv1', 'shadow_primary', 0, 0.88, ?, ?)",
        (now, now),
    )
    await phase5_db.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) VALUES "
        "('sv1', 'sk1', 1, 'capability_name: product_watch\\nversion: 1\\nsteps: []\\n', "
        "'{}', '{}', 'seed', 'v1', ?)",
        (now,),
    )
    await phase5_db.commit()

    auto_id = await _seed_automation(phase5_db, next_run_offset_minutes=-5)
    repo = AutomationRepository(phase5_db)
    auto = await repo.get(auto_id)

    # Mock skill executor factory — returns succeeded result with price output
    skill_result = MagicMock()
    skill_result.status = "succeeded"
    skill_result.final_output = {"price_usd": 42}
    skill_result.total_cost_usd = 0.0

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=skill_result)

    router = AsyncMock()
    router.complete = AsyncMock()  # should NOT be called

    dispatcher, repo, router, notifier = _make_dispatcher(
        phase5_db,
        router=router,
        skill_factory=lambda: executor,
    )

    report = await dispatcher.dispatch(auto)

    assert report.outcome == "succeeded"

    # Verify execution_path is "skill"
    runs = await repo.list_runs(auto_id)
    assert len(runs) == 1
    assert runs[0].execution_path == "skill"

    # Verify router.complete was NOT called (skill path bypasses Claude)
    router.complete.assert_not_awaited()


# ---------------------------------------------------------------------------
# AS-5.4: Alert fires when condition is true.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_as_5_4_alert_fires_when_condition_true(phase5_db):
    from donna.automations.repository import AutomationRepository

    class _Meta:
        invocation_id = "inv-alert"
        cost_usd = 0.02

    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"price_usd": 89}, _Meta()))

    notifier = AsyncMock()
    notifier.dispatch = AsyncMock(return_value=True)

    # Alert fires when price_usd <= 100 — output has price_usd=89, so it should fire
    auto_id = await _seed_automation(
        phase5_db,
        alert_conditions={"all_of": [{"field": "price_usd", "op": "<=", "value": 100}]},
        next_run_offset_minutes=-5,
    )
    repo = AutomationRepository(phase5_db)
    auto = await repo.get(auto_id)

    dispatcher, repo, router, notifier = _make_dispatcher(
        phase5_db, router=router, notifier=notifier
    )

    report = await dispatcher.dispatch(auto)

    assert report.outcome == "succeeded"
    assert report.alert_sent is True

    # Notifier dispatched exactly once
    notifier.dispatch.assert_awaited_once()

    # Check automation_run row
    runs = await repo.list_runs(auto_id)
    assert len(runs) == 1
    run = runs[0]
    assert run.alert_sent is True
    # alert_content must mention the price value
    assert run.alert_content is not None
    assert "89" in run.alert_content


# ---------------------------------------------------------------------------
# AS-5.5: 5 consecutive failures pause the automation.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_as_5_5_five_consecutive_failures_pause_automation(phase5_db):
    from donna.automations.repository import AutomationRepository
    from donna.config import SkillSystemConfig
    from donna.notifications.service import NOTIF_AUTOMATION_FAILURE

    threshold = SkillSystemConfig().automation_failure_pause_threshold
    assert threshold == 5, f"test assumes threshold=5, got {threshold}"

    router = AsyncMock()
    router.complete = AsyncMock(side_effect=RuntimeError("upstream unavailable"))

    notifier = AsyncMock()
    notifier.dispatch = AsyncMock(return_value=True)

    auto_id = await _seed_automation(phase5_db, next_run_offset_minutes=-5)
    repo = AutomationRepository(phase5_db)

    dispatcher, repo, router, notifier = _make_dispatcher(
        phase5_db, router=router, notifier=notifier
    )

    for _ in range(threshold):
        auto = await repo.get(auto_id)
        # Re-set next_run_at to the past so it remains dispatchable
        await repo.update_fields(
            auto_id,
            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        auto = await repo.get(auto_id)
        await dispatcher.dispatch(auto)

    # After 5 failures the automation must be paused
    updated = await repo.get(auto_id)
    assert updated.status == "paused", (
        f"expected 'paused', got {updated.status!r} after {threshold} failures"
    )

    # Notifier must have been called at least once with automation_failure type
    assert notifier.dispatch.await_count >= 1
    call_kwargs_list = [call.kwargs for call in notifier.dispatch.await_args_list]
    failure_calls = [
        kw for kw in call_kwargs_list
        if kw.get("notification_type") == NOTIF_AUTOMATION_FAILURE
    ]
    assert len(failure_calls) >= 1, (
        f"expected at least one {NOTIF_AUTOMATION_FAILURE!r} call; "
        f"got: {call_kwargs_list}"
    )
