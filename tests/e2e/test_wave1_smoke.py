"""Wave 1 E2E smoke tests — four scenarios per spec §6.5.

These tests exercise the Wave 1 production-enablement flow end-to-end
against the harness in :mod:`tests.e2e.harness`. All LLM calls route to
fakes and the Discord bot is a stub — the assertions focus on durable
state written to SQLite (skill state transitions, automation runs,
candidate reports, etc.) and on the fake bot's recorded sends.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_invocation_log(
    conn,
    *,
    task_type: str,
    cost_usd: float,
    at: str,
    model_alias: str = "claude_sonnet",
    user_id: str = "nick",
    output: str | None = None,
    is_shadow: int = 0,
) -> str:
    """Insert a row into invocation_log, matching the initial-schema shape.

    Column order (see alembic/versions/6c29a416f050_initial_schema.py):
        id, timestamp, task_type, task_id, model_alias, model_actual,
        input_hash, latency_ms, tokens_in, tokens_out, cost_usd, output,
        quality_score, is_shadow, eval_session_id, spot_check_queued, user_id
    """
    inv_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO invocation_log "
        "(id, timestamp, task_type, task_id, model_alias, model_actual, "
        " input_hash, latency_ms, tokens_in, tokens_out, cost_usd, output, "
        " quality_score, is_shadow, eval_session_id, spot_check_queued, user_id) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 0, ?)",
        (
            inv_id, at, task_type, model_alias, model_alias,
            "hash", 100, 500, 200, cost_usd, output,
            is_shadow, user_id,
        ),
    )
    return inv_id


async def _insert_capability(
    conn, *, name: str, description: str = "", trigger_type: str = "on_message"
) -> str:
    cap_id = str(uuid.uuid4())
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        (
            cap_id, name, description,
            json.dumps({"type": "object"}), trigger_type, now,
        ),
    )
    return cap_id


async def _insert_skill_with_version(
    conn,
    *,
    skill_id: str,
    version_id: str,
    capability_name: str,
    state: str,
    baseline_agreement: float | None = None,
) -> None:
    """Insert a skill, its version, and wire current_version_id."""
    now = datetime.now(tz=UTC).isoformat()
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, NULL, ?, 0, ?, ?, ?)",
        (skill_id, capability_name, state, baseline_agreement, now, now),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, "
        "yaml_backbone, step_content, output_schemas, created_by, "
        "changelog, created_at) VALUES (?, ?, 1, ?, ?, ?, 'seed', NULL, ?)",
        (version_id, skill_id, "steps: []", "{}", "{}", now),
    )
    await conn.execute(
        "UPDATE skill SET current_version_id = ? WHERE id = ?",
        (version_id, skill_id),
    )


# ---------------------------------------------------------------------------
# Scenario 1 — nightly cycle produces a drafted skill.
# ---------------------------------------------------------------------------


async def test_nightly_cycle_drafts_skill(runtime) -> None:
    from donna.skills.crons import NightlyDeps, run_nightly_tasks

    conn = runtime.db.connection
    task_type = "test_capability_high_volume"
    now = datetime.now(tz=UTC)

    await _insert_capability(
        conn, name=task_type, description="High-volume test capability"
    )

    # 200 invocations at $0.10 each, spread over the last 10 days.
    # expected_savings = 200 * 0.10 * 0.85 = $17 > default threshold $5.
    for i in range(200):
        at = (now - timedelta(days=10) + timedelta(minutes=i)).isoformat()
        await _insert_invocation_log(
            conn, task_type=task_type, cost_usd=0.10, at=at,
            output=json.dumps({"title": f"row-{i}"}),
        )
    await conn.commit()

    # Canned Claude response for skill generation. TASK_TYPE constant from
    # src/donna/skills/auto_drafter.py is "skill_auto_draft".
    runtime.fake_claude.canned["skill_auto_draft"] = {
        "skill_yaml": "steps: []\n",
        "step_prompts": {"extract": "extract the thing"},
        "output_schemas": {
            "extract": {"type": "object"},
        },
        "fixtures": [
            {
                "case_name": "c1",
                "input": {"raw": "foo"},
                "expected_output_shape": {"type": "object"},
                "tool_mocks": {},
            },
        ],
    }

    deps = NightlyDeps(
        detector=runtime.skill_bundle.detector,
        auto_drafter=runtime.skill_bundle.auto_drafter,
        degradation=runtime.skill_bundle.degradation,
        evolution_scheduler=runtime.skill_bundle.evolution_scheduler,
        correction_cluster=runtime.skill_bundle.correction_cluster,
        cost_tracker=runtime.cost_tracker,
        daily_budget_limit_usd=100.0,
        config=runtime.skill_config,
    )
    await run_nightly_tasks(deps)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM skill_candidate_report WHERE status = 'drafted'"
    )
    assert (await cursor.fetchone())[0] >= 1, (
        "nightly cycle should mark at least one candidate report as 'drafted'"
    )

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM skill_version WHERE created_by = 'claude_auto_draft'"
    )
    assert (await cursor.fetchone())[0] >= 1, (
        "auto-drafter should persist at least one skill_version with "
        "created_by='claude_auto_draft'"
    )

    cursor = await conn.execute("SELECT COUNT(*) FROM skill_state_transition")
    assert (await cursor.fetchone())[0] >= 1, (
        "auto-drafter should emit at least one skill_state_transition row "
        "(claude_native -> skill_candidate -> draft)"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — automation tick fires alert.
# ---------------------------------------------------------------------------


async def test_automation_tick_alerts(runtime) -> None:
    from donna.automations.repository import AutomationRepository

    conn = runtime.db.connection
    now = datetime.now(tz=UTC)

    # Canned claude_native response: the dispatcher calls the router with
    # task_type=automation.capability_name (see AutomationDispatcher.dispatch
    # in src/donna/automations/dispatcher.py line ~110). FakeRouter routes
    # non-chat/non-skill_validation task_types to FakeClaude, so we key the
    # canned dict by "product_watch".
    runtime.fake_claude.canned["product_watch"] = {
        "ok": True,
        "price_usd": 50.0,
        "in_stock": True,
    }

    # product_watch capability is seeded by the seed_product_watch_capability
    # migration (alembic rev d0e1f2a3b4c5). No need to insert it here — doing
    # so would trip the UNIQUE(capability.name) constraint.
    await conn.commit()

    repo = AutomationRepository(conn)
    automation_id = await repo.create(
        user_id="nick",
        name="Watch COS shirt",
        description=None,
        capability_name="product_watch",
        inputs={"url": "https://cos.com/shirt"},
        trigger_type="on_schedule",
        schedule="0 * * * *",
        alert_conditions={"all_of": [{"field": "ok", "op": "==", "value": True}]},
        alert_channels=["tasks"],
        max_cost_per_run_usd=1.0,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=now - timedelta(minutes=5),
    )

    await runtime.automation_scheduler.run_once()

    cursor = await conn.execute(
        "SELECT status, alert_sent FROM automation_run "
        "WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, f"expected exactly one automation_run row, got {len(rows)}"
    assert rows[0][0] == "succeeded"
    assert rows[0][1] == 1, "alert_sent flag should be set"

    assert len(runtime.fake_bot.sends) >= 1, (
        "FakeDonnaBot should have received at least one send"
    )
    kind, target, content = runtime.fake_bot.sends[0]
    assert kind == "channel"
    assert target == "tasks"
    assert content  # non-empty alert message


# ---------------------------------------------------------------------------
# Scenario 3 — sandbox promotes to shadow_primary.
# ---------------------------------------------------------------------------


async def test_sandbox_promotes_to_shadow_primary(runtime) -> None:
    conn = runtime.db.connection
    now = datetime.now(tz=UTC).isoformat()

    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    await _insert_capability(conn, name="promo_cap", description="Promotion test")
    await _insert_skill_with_version(
        conn, skill_id=skill_id, version_id=version_id,
        capability_name="promo_cap", state="sandbox",
    )

    # 20 successful skill_run rows — crosses sandbox_promotion_min_runs=20
    # with validity_rate=1.0 >= threshold 0.90.
    for _ in range(20):
        run_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, final_output, started_at, finished_at, user_id) "
            "VALUES (?, ?, ?, 'succeeded', '{}', ?, ?, ?, 'nick')",
            (
                run_id, skill_id, version_id,
                json.dumps({"ok": True}), now, now,
            ),
        )
    await conn.commit()

    # lifecycle promotion entry point (see src/donna/skills/lifecycle.py:224
    # `check_and_promote_if_eligible`).
    lifecycle = runtime.skill_bundle.lifecycle_manager
    new_state = await lifecycle.check_and_promote_if_eligible(skill_id)
    assert new_state == "shadow_primary", (
        f"expected promotion to shadow_primary, got {new_state!r}"
    )

    cursor = await conn.execute(
        "SELECT state FROM skill WHERE id = ?", (skill_id,),
    )
    assert (await cursor.fetchone())[0] == "shadow_primary"

    cursor = await conn.execute(
        "SELECT from_state, to_state, reason FROM skill_state_transition "
        "WHERE skill_id = ? ORDER BY at DESC LIMIT 1",
        (skill_id,),
    )
    trans = await cursor.fetchone()
    assert trans is not None, "expected a skill_state_transition row"
    assert trans[0] == "sandbox"
    assert trans[1] == "shadow_primary"
    assert trans[2] == "gate_passed"


# ---------------------------------------------------------------------------
# Scenario 4 — trusted degrades to flagged_for_review.
# ---------------------------------------------------------------------------


async def test_trusted_degrades_to_flagged(runtime) -> None:
    conn = runtime.db.connection
    now = datetime.now(tz=UTC)

    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    await _insert_capability(conn, name="degrade_cap", description="Degrade test")
    await _insert_skill_with_version(
        conn, skill_id=skill_id, version_id=version_id,
        capability_name="degrade_cap", state="trusted",
        baseline_agreement=0.90,
    )
    await conn.commit()

    # 30 shadow divergences with overall_agreement=0.65. With the default
    # degradation_rolling_window=30 and degradation_agreement_threshold=0.5,
    # every divergence counts as a success (0.65 >= 0.5), giving us 30/30
    # current successes. The Wilson 95% CI upper bound at 30/30 is ~1.0, so
    # this alone would NOT flag. We seed 20 "failures" below 0.5 instead.
    #
    # Flip the approach: seed divergences at 0.30 agreement so every one
    # counts as a FAILURE against the threshold. 0/30 successes → Wilson CI
    # upper ~0.12, well below the 0.90 baseline → flagged.
    for i in range(30):
        run_id = str(uuid.uuid4())
        div_id = str(uuid.uuid4())
        at = (now - timedelta(hours=i)).isoformat()
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, started_at, finished_at, user_id) "
            "VALUES (?, ?, ?, 'succeeded', '{}', ?, ?, 'nick')",
            (run_id, skill_id, version_id, at, at),
        )
        await _insert_invocation_log(
            conn, task_type="shadow_claude", cost_usd=0.05, at=at,
            user_id="system", is_shadow=1,
        )
        await conn.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, "
            "shadow_invocation_id, overall_agreement, diff_summary, "
            "flagged_for_evolution, created_at) "
            "VALUES (?, ?, ?, ?, '{}', 0, ?)",
            (div_id, run_id, str(uuid.uuid4()), 0.30, at),
        )
    await conn.commit()

    # DegradationDetector.run() iterates all trusted skills.
    reports = await runtime.skill_bundle.degradation.run()
    flagged = [r for r in reports if r.skill_id == skill_id]
    assert flagged, "DegradationDetector should have evaluated our skill"
    assert flagged[0].outcome == "flagged", (
        f"expected outcome='flagged', got {flagged[0].outcome!r}"
    )

    cursor = await conn.execute(
        "SELECT state FROM skill WHERE id = ?", (skill_id,),
    )
    assert (await cursor.fetchone())[0] == "flagged_for_review"

    cursor = await conn.execute(
        "SELECT to_state FROM skill_state_transition WHERE skill_id = ? "
        "ORDER BY at DESC LIMIT 1",
        (skill_id,),
    )
    assert (await cursor.fetchone())[0] == "flagged_for_review"
