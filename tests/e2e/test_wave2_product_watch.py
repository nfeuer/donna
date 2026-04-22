"""Wave 2 E2E: product_watch automation runs end-to-end through claude_native path."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.asyncio
async def test_product_watch_automation_tick_fires_alert(runtime) -> None:
    """Create a product_watch automation, tick the scheduler, assert alert.

    product_watch skill is seeded by the seed_product_watch_capability migration
    (lifecycle state = sandbox). Wave 2 Day 1 exercise: claude_native dispatch
    path, since skill.state < shadow_primary. Once the skill promotes to
    shadow_primary (after 20 schema-valid shadow runs), automation execution
    will transparently switch to the skill path.
    """
    conn = runtime.db.connection

    # Verify the seed migration ran — product_watch capability + sandbox skill exist.
    cursor = await conn.execute(
        "SELECT state FROM skill WHERE capability_name = 'product_watch'"
    )
    row = await cursor.fetchone()
    assert row is not None, "product_watch skill should be seeded by migration"
    assert row[0] == "sandbox", f"expected sandbox, got {row[0]}"

    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()

    # Canned claude_native output. Dispatcher uses task_type=capability_name.
    runtime.fake_claude.canned["product_watch"] = {
        "ok": True,
        "price_usd": 79.0,
        "currency": "USD",
        "in_stock": True,
        "size_available": True,
        "triggers_alert": True,
        "title": "Blue Shirt",
    }

    # Create the automation.
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', 'Watch Blue Shirt', NULL, 'product_watch', ?, "
        "'on_schedule', '0 * * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            json.dumps({
                "url": "https://example-shop.com/shirt-blue",
                "max_price_usd": 100.0,
                "required_size": "L",
            }),
            json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(), now.isoformat(),
        ),
    )
    await conn.commit()

    await runtime.automation_scheduler.run_once()

    # Assert the automation_run was recorded and alert fired.
    cursor = await conn.execute(
        "SELECT status, alert_sent, execution_path FROM automation_run "
        "WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, f"expected 1 run, got {len(rows)}"
    status, alert_sent, exec_path = rows[0]
    assert status == "succeeded"
    assert alert_sent == 1
    assert exec_path == "claude_native"  # sandbox skill doesn't run user traffic

    # Assert alert was dispatched via NotificationService -> FakeDonnaBot.
    assert len(runtime.fake_bot.sends) >= 1, (
        f"expected at least 1 bot send; got {runtime.fake_bot.sends}"
    )
    kind, target, content = runtime.fake_bot.sends[0]
    assert kind == "channel"
    assert target == "tasks"
    assert content  # non-empty


@pytest.mark.asyncio
async def test_product_watch_alert_does_not_fire_when_condition_false(runtime) -> None:
    """When triggers_alert=False in the run output, NO Discord message."""
    conn = runtime.db.connection
    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()

    runtime.fake_claude.canned["product_watch"] = {
        "ok": True,
        "price_usd": 189.0,
        "in_stock": True,
        "size_available": True,
        "triggers_alert": False,  # Price above threshold.
        "title": "Grey Coat",
    }

    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', 'Watch Grey Coat', NULL, 'product_watch', ?, "
        "'on_schedule', '0 * * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            json.dumps({"url": "https://example-shop.com/coat-grey",
                        "max_price_usd": 100.0, "required_size": "L"}),
            json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(), now.isoformat(),
        ),
    )
    await conn.commit()

    sends_before = len(runtime.fake_bot.sends)
    await runtime.automation_scheduler.run_once()

    cursor = await conn.execute(
        "SELECT status, alert_sent FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "succeeded"
    assert row[1] == 0

    # No new Discord sends for this automation.
    assert len(runtime.fake_bot.sends) == sends_before


@pytest.mark.asyncio
async def test_product_watch_runs_via_skill_executor_at_shadow_primary(runtime) -> None:
    """F-W2-G: once product_watch is promoted to shadow_primary,
    AutomationDispatcher routes to SkillExecutor (not claude_native),
    and automation_run.skill_run_id is populated.

    This scenario proves the dormant skill-executor path activates at
    shadow_primary. It uses a stub SkillExecutor that writes a real
    skill_run row via SkillRunRepository so the linkage to
    automation_run.skill_run_id is exercised end-to-end against the
    migrated schema, while the real AutomationDispatcher still performs
    path selection, run insertion, alert evaluation, and scheduling.
    """
    from donna.skills.executor import SkillRunResult
    from donna.skills.run_persistence import SkillRunRepository
    from donna.tasks.db_models import SkillState

    conn = runtime.db.connection

    # --- 1. Locate the seeded product_watch skill + promote via lifecycle. -----
    cursor = await conn.execute(
        "SELECT id, current_version_id, state FROM skill "
        "WHERE capability_name = 'product_watch'"
    )
    skill_row = await cursor.fetchone()
    assert skill_row is not None, "product_watch skill should be seeded"
    skill_id, _version_id, initial_state = skill_row
    assert initial_state == "sandbox", f"expected sandbox, got {initial_state}"

    # Promote sandbox -> shadow_primary through the real lifecycle (human_approval
    # path avoids gate counter pre-seeding; the routing behaviour under test is
    # orthogonal to how the skill arrives at shadow_primary).
    lifecycle = runtime.skill_bundle.lifecycle_manager
    await lifecycle.transition(
        skill_id=skill_id,
        to_state=SkillState.SHADOW_PRIMARY,
        reason="human_approval",
        actor="user",
        actor_id="test-nick",
        notes="F-W2-G e2e",
    )

    cursor = await conn.execute(
        "SELECT state FROM skill WHERE id = ?", (skill_id,),
    )
    assert (await cursor.fetchone())[0] == "shadow_primary"

    # --- 2. Wire a real SkillExecutor-shaped stub into the dispatcher. --------
    # The harness default is `skill_executor_factory=lambda: None`; swap in a
    # stub that writes a real skill_run row and returns a populated
    # SkillRunResult. This exercises the dispatcher's skill-path branch
    # (lines 98-108 of dispatcher.py) and the bidirectional run linkage.
    run_repo = SkillRunRepository(conn)

    canned_output = {
        "ok": True,
        "price_usd": 79.0,
        "currency": "USD",
        "in_stock": True,
        "size_available": True,
        "triggers_alert": True,
        "title": "Blue Shirt",
    }

    executor_calls: list[dict] = []

    class _StubSkillExecutor:
        async def execute(self, *, skill, version, inputs, user_id,
                          task_id=None, automation_run_id=None, **_kw):
            executor_calls.append({
                "skill_id": skill.id,
                "capability": skill.capability_name,
                "automation_run_id": automation_run_id,
                "inputs": inputs,
            })
            run_id = await run_repo.start_run(
                skill_id=skill.id,
                skill_version_id=version.id,
                inputs=inputs,
                user_id=user_id,
                task_id=task_id,
                automation_run_id=automation_run_id,
            )
            result = SkillRunResult(
                status="succeeded",
                final_output=canned_output,
                state={"extract_product_info": canned_output,
                       "format_output": canned_output},
                total_latency_ms=5,
                total_cost_usd=0.0,
                run_id=run_id,
            )
            await run_repo.finish_run(
                skill_run_id=run_id,
                status=result.status,
                final_output=result.final_output,
                state_object=result.state,
                tool_result_cache={},
                total_latency_ms=result.total_latency_ms,
                total_cost_usd=result.total_cost_usd,
                escalation_reason=None,
                error=None,
            )
            return result

    # Replace the dispatcher's factory. Touching the private attr is the
    # minimally invasive way to opt into the skill path from this single test
    # without reshaping the harness for all callers.
    runtime.automation_dispatcher._skill_executor_factory = _StubSkillExecutor

    # --- 3. Create an automation that is due now. ------------------------------
    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', 'Skill-Path Watch', NULL, 'product_watch', ?, "
        "'on_schedule', '0 * * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            json.dumps({
                "url": "https://example-shop.com/shirt-blue",
                "max_price_usd": 100.0,
                "required_size": "L",
            }),
            json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(), now.isoformat(),
        ),
    )
    await conn.commit()

    sends_before = len(runtime.fake_bot.sends)

    # --- 4. Tick the scheduler. -------------------------------------------------
    await runtime.automation_scheduler.run_once()

    # --- 5. Assertions: dispatcher chose skill path + linkage is recorded. ----
    assert len(executor_calls) == 1, (
        f"expected 1 skill execution, got {len(executor_calls)}. "
        "If zero, the dispatcher did NOT route to the skill path at shadow_primary — "
        "this is the F-W2-G dormant-path regression."
    )
    assert executor_calls[0]["capability"] == "product_watch"
    assert executor_calls[0]["automation_run_id"] is not None

    cursor = await conn.execute(
        "SELECT status, alert_sent, execution_path, skill_run_id "
        "FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    status, alert_sent, exec_path, skill_run_id_val = rows[0]
    assert status == "succeeded", f"run status={status!r}"
    assert exec_path == "skill", (
        f"expected execution_path='skill' at shadow_primary, got {exec_path!r}"
    )
    assert skill_run_id_val is not None, (
        "automation_run.skill_run_id must be populated on skill-path runs"
    )
    assert alert_sent == 1

    # Bidirectional linkage: the skill_run row points back at the automation_run.
    cursor = await conn.execute(
        "SELECT automation_run_id, status FROM skill_run WHERE id = ?",
        (skill_run_id_val,),
    )
    sr = await cursor.fetchone()
    assert sr is not None, "skill_run row not persisted"
    assert sr[1] == "succeeded"
    cursor = await conn.execute(
        "SELECT id FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    automation_run_row_id = (await cursor.fetchone())[0]
    assert sr[0] == automation_run_row_id, (
        "skill_run.automation_run_id must equal the automation_run.id"
    )

    # Alert was dispatched via NotificationService -> FakeDonnaBot.
    assert len(runtime.fake_bot.sends) == sends_before + 1
    kind, target, content = runtime.fake_bot.sends[-1]
    assert kind == "channel"
    assert target == "tasks"
    assert content
