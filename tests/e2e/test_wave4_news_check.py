"""Wave 4 E2E — news_check: NL creation, since-last-run filter, shadow promotion.

Three acceptance scenarios from the Wave 4 spec:

AS-W4.1 — Seed a news_check automation, tick the scheduler with a canned
           claude_native response that returns 2 matching items.  Assert
           automation_run.status=succeeded, alert_sent=1, FakeDonnaBot DM.

AS-W4.2 — Second tick: prior_run_end is populated from the first run's
           finished_at.  Canned response has triggers_alert=False (no new
           items).  Assert no DM is sent.

AS-W4.3 — Promote news_check to shadow_primary via SkillLifecycleManager.
           Wire a stub SkillExecutor that patches rss_fetch in a fresh
           ToolRegistry, canned FakeRouter responses for classify_items and
           render_digest steps, and a real SkillRunRepository.  Tick the
           scheduler.  Assert execution_path='skill', skill_run_id populated,
           and alert_sent=1.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Canned RSS responses (used by the skill-executor path in AS-W4.3).
# ---------------------------------------------------------------------------

RSS_RESPONSE_NEW = {
    "ok": True,
    "feed_title": "AI Safety Daily",
    "feed_description": "",
    "items": [
        {
            "title": "Alignment interpretability paper",
            "link": "https://example.com/a1",
            "published": "2026-04-20T08:00:00+00:00",
            "author": "alice",
            "summary": "Scalable interpretability.",
        },
        {
            "title": "Policy brief: AI safety",
            "link": "https://example.com/a2",
            "published": "2026-04-20T06:00:00+00:00",
            "author": "bob",
            "summary": "Regulatory overview.",
        },
    ],
}

RSS_RESPONSE_NONE = {"ok": True, "feed_title": "AI Safety Daily", "items": []}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _news_check_automation_values(
    automation_id: str,
    now: datetime,
    past: str,
) -> tuple:
    """Return the positional VALUES tuple for an INSERT into automation."""
    return (
        automation_id,
        json.dumps({
            "feed_urls": ["https://example.com/ai-safety-feed"],
            "topics": ["AI safety", "alignment"],
        }),
        json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
        json.dumps(["tasks"]),
        past,
        now.isoformat(),
        now.isoformat(),
    )


async def _insert_news_check_automation(
    conn,
    automation_id: str,
    now: datetime,
    past: str,
    name: str = "Watch AI Safety Feed",
) -> None:
    """Insert a due news_check automation row into the test DB."""
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', ?, NULL, 'news_check', ?, "
        "'on_schedule', '0 */12 * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            name,
            *_news_check_automation_values(automation_id, now, past)[1:],
        ),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# AS-W4.1 — NL creation → first-run alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_news_check_nl_creation_then_first_tick_alerts(runtime) -> None:
    """AS-W4.1 — Seed automation directly (NL creation simplified per guidance),
    first tick → claude_native → 2-item digest → DM.

    The core point is dispatch + alert, not NL parsing.  We seed the
    automation row directly (matching the 'Before You Begin' guidance that
    simplifying AS-W4.1 to direct seeding is acceptable).
    """
    conn = runtime.db.connection

    # Verify the seed migration ran — news_check capability + sandbox skill exist.
    cursor = await conn.execute(
        "SELECT state FROM skill WHERE capability_name = 'news_check'"
    )
    row = await cursor.fetchone()
    assert row is not None, "news_check skill should be seeded by migration"
    assert row[0] == "sandbox", f"expected sandbox, got {row[0]}"

    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()

    # Canned claude_native response.  Dispatcher uses task_type=capability_name.
    runtime.fake_claude.canned["news_check"] = {
        "ok": True,
        "triggers_alert": True,
        "message": (
            "2 new AI safety items:\n- Alignment interpretability paper\n"
            "- Policy brief: AI safety"
        ),
        "meta": {
            "item_count": 2,
            "action_required_count": 2,
            "source_feed": "AI Safety Daily",
        },
    }

    automation_id = str(uuid.uuid4())
    await _insert_news_check_automation(conn, automation_id, now, past)

    await runtime.automation_scheduler.run_once()

    # Assert automation_run was recorded and alert fired.
    cursor = await conn.execute(
        "SELECT status, alert_sent, execution_path FROM automation_run "
        "WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, f"expected 1 run, got {len(rows)}"
    status, alert_sent, exec_path = rows[0]
    assert status == "succeeded", f"run status={status!r}"
    assert alert_sent == 1, "alert should fire when triggers_alert=True"
    assert exec_path == "claude_native", (
        f"sandbox skill must use claude_native, got {exec_path!r}"
    )

    # Assert alert was dispatched via NotificationService -> FakeDonnaBot.
    assert len(runtime.fake_bot.sends) >= 1, (
        f"expected at least 1 bot send; got {runtime.fake_bot.sends}"
    )
    kind, target, content = runtime.fake_bot.sends[-1]
    assert kind == "channel"
    assert target == "tasks"
    assert content, "alert content must be non-empty"


# ---------------------------------------------------------------------------
# AS-W4.2 — Since-last-run filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_news_check_second_tick_filters_by_prior_run_end(runtime) -> None:
    """AS-W4.2 — Second tick with prior_run_end populated → 0 items → no DM.

    We insert a prior succeeded automation_run row so that
    dispatcher._query_prior_run_end() finds a non-None timestamp.  The
    canned response for this tick has triggers_alert=False (empty matches).
    """
    conn = runtime.db.connection

    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()
    prior_run_time = (now - timedelta(hours=12)).isoformat()

    # First run response (triggers alert = True, already happened 12h ago).
    runtime.fake_claude.canned["news_check"] = {
        "ok": True,
        "triggers_alert": True,
        "message": "1 new item found.",
        "meta": {
            "item_count": 1,
            "action_required_count": 1,
            "source_feed": "AI Safety Daily",
        },
    }

    automation_id = str(uuid.uuid4())
    await _insert_news_check_automation(
        conn, automation_id, now, past, name="Watch AI Safety Feed (second-tick)"
    )

    # Seed a prior succeeded run so prior_run_end is populated on the next dispatch.
    prior_run_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation_run "
        "(id, automation_id, status, started_at, finished_at, "
        " execution_path, output, alert_sent, alert_content, "
        " invocation_log_id, skill_run_id, error, cost_usd) "
        "VALUES (?, ?, 'succeeded', ?, ?, 'claude_native', ?, 1, NULL, "
        "        NULL, NULL, NULL, 0.01)",
        (
            prior_run_id,
            automation_id,
            prior_run_time,
            prior_run_time,
            json.dumps({
                "ok": True, "triggers_alert": True,
                "message": "1 item", "meta": {"item_count": 1,
                                               "action_required_count": 1,
                                               "source_feed": "AI Safety Daily"},
            }),
        ),
    )
    await conn.commit()

    # Now switch canned response to no new items for the second tick.
    runtime.fake_claude.canned["news_check"] = {
        "ok": True,
        "triggers_alert": False,
        "message": None,
        "meta": {
            "item_count": 0,
            "action_required_count": 0,
            "source_feed": "AI Safety Daily",
        },
    }

    sends_before = len(runtime.fake_bot.sends)
    await runtime.automation_scheduler.run_once()

    # Assert the run recorded no alert.
    cursor = await conn.execute(
        "SELECT status, alert_sent, execution_path FROM automation_run "
        "WHERE automation_id = ? AND id != ?",
        (automation_id, prior_run_id),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, f"expected 1 new run row, got {len(rows)}"
    status, alert_sent, _exec_path = rows[0]
    assert status == "succeeded", f"run status={status!r}"
    assert alert_sent == 0, "no alert should fire when triggers_alert=False"

    # No new Discord sends.
    assert len(runtime.fake_bot.sends) == sends_before, (
        f"expected no new sends; got {runtime.fake_bot.sends[sends_before:]}"
    )

    # Verify prior_run_end was used: the prompt passed to claude should contain
    # the prior_run_time so the canned response was selected on the second tick.
    # (We rely on the invocation being recorded in fake_claude.invocations.)
    invocations = [
        inv for inv in runtime.fake_claude.invocations
        if inv.task_type == "news_check"
    ]
    assert len(invocations) >= 1, "FakeClaude should have received a news_check call"
    last_inv = invocations[-1]
    # The dispatcher embeds prior_run_end in the prompt JSON.
    assert prior_run_time in last_inv.prompt, (
        f"prior_run_end={prior_run_time!r} should appear in the prompt; "
        f"prompt was: {last_inv.prompt[:300]}"
    )


# ---------------------------------------------------------------------------
# AS-W4.3 — Promotion to shadow_primary fires SkillExecutor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_news_check_promotion_to_shadow_primary_fires_skill_executor(
    runtime,
) -> None:
    """AS-W4.3 — Promote to shadow_primary → SkillExecutor path.

    Uses a stub SkillExecutor (same pattern as test_wave2) that:
    - Builds a real ToolRegistry with rss_fetch patched to return RSS_RESPONSE_NEW.
    - Provides FakeRouter canned responses for classify_items + render_digest.
    - Writes a real skill_run row via SkillRunRepository.
    - Returns a SkillRunResult with the final digest output.

    Asserts: execution_path='skill', skill_run_id populated, alert_sent=1.
    """
    from donna.skills.run_persistence import SkillRunRepository
    from donna.skills.tool_registry import ToolRegistry
    from donna.tasks.db_models import SkillState

    conn = runtime.db.connection

    # --- 1. Locate the seeded news_check skill + promote via lifecycle. ---------
    cursor = await conn.execute(
        "SELECT id, current_version_id, state FROM skill "
        "WHERE capability_name = 'news_check'"
    )
    skill_row = await cursor.fetchone()
    assert skill_row is not None, "news_check skill should be seeded by migration"
    skill_id, _version_id, initial_state = skill_row
    assert initial_state == "sandbox", f"expected sandbox, got {initial_state}"

    lifecycle = runtime.skill_bundle.lifecycle_manager
    await lifecycle.transition(
        skill_id=skill_id,
        to_state=SkillState.SHADOW_PRIMARY,
        reason="human_approval",
        actor="user",
        actor_id="test-nick",
        notes="AS-W4.3 e2e",
    )

    cursor = await conn.execute(
        "SELECT state FROM skill WHERE id = ?", (skill_id,),
    )
    assert (await cursor.fetchone())[0] == "shadow_primary"

    # --- 2. Canned FakeRouter responses for the two LLM steps. ------------------
    # SkillExecutor calls complete() with task_type="skill_step::news_check::<step>".
    # FakeRouter routes everything except chat_* / skill_validation::* to FakeClaude.
    runtime.fake_claude.canned["skill_step::news_check::classify_items"] = {
        "matches": [
            {
                "title": "Alignment interpretability paper",
                "link": "https://example.com/a1",
                "published": "2026-04-20T08:00:00+00:00",
                "summary_short": "Scalable interpretability.",
                "matched_topics": ["AI safety", "alignment"],
            },
            {
                "title": "Policy brief: AI safety",
                "link": "https://example.com/a2",
                "published": "2026-04-20T06:00:00+00:00",
                "summary_short": "Regulatory overview.",
                "matched_topics": ["AI safety"],
            },
        ],
        "total_scanned": 2,
        "total_matched": 2,
    }
    runtime.fake_claude.canned["skill_step::news_check::render_digest"] = {
        "ok": True,
        "triggers_alert": True,
        "message": (
            "2 new AI safety items:\n- Alignment interpretability paper\n"
            "- Policy brief: AI safety"
        ),
        "meta": {
            "item_count": 2,
            "action_required_count": 2,
            "source_feed": "AI Safety Daily",
        },
    }

    # --- 3. Build a stub SkillExecutor that patches rss_fetch. ------------------
    # Create a fresh isolated ToolRegistry with rss_fetch replaced by a fake
    # that returns RSS_RESPONSE_NEW without making HTTP calls.
    fake_tool_registry = ToolRegistry()

    async def _fake_rss_fetch(**kwargs):
        return RSS_RESPONSE_NEW

    fake_tool_registry.register("rss_fetch", _fake_rss_fetch)

    run_repo = SkillRunRepository(conn)
    executor_calls: list[dict] = []

    from donna.skills.executor import SkillExecutor

    real_executor = SkillExecutor(
        model_router=runtime.fake_router,
        tool_registry=fake_tool_registry,
        run_repository=run_repo,
    )

    class _WrapExecutor:
        """Thin wrapper that records invocations and delegates to real executor."""

        async def execute(self, *, skill, version, inputs, user_id,
                          task_id=None, automation_run_id=None, **_kw):
            executor_calls.append({
                "skill_id": skill.id,
                "capability": skill.capability_name,
                "automation_run_id": automation_run_id,
                "inputs": inputs,
            })
            return await real_executor.execute(
                skill=skill,
                version=version,
                inputs=inputs,
                user_id=user_id,
                task_id=task_id,
                automation_run_id=automation_run_id,
            )

    runtime.automation_dispatcher._skill_executor_factory = _WrapExecutor

    # --- 4. Create a due automation. --------------------------------------------
    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()
    automation_id = str(uuid.uuid4())
    await _insert_news_check_automation(
        conn, automation_id, now, past, name="Skill-Path news_check"
    )

    sends_before = len(runtime.fake_bot.sends)

    # --- 5. Tick the scheduler. -------------------------------------------------
    await runtime.automation_scheduler.run_once()

    # --- 6. Assert skill path was taken. ----------------------------------------
    assert len(executor_calls) == 1, (
        f"expected 1 skill execution, got {len(executor_calls)}. "
        "If zero, dispatcher did NOT route to skill path at shadow_primary."
    )
    assert executor_calls[0]["capability"] == "news_check"
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
    assert alert_sent == 1, "alert should fire on successful news_check skill run"

    # Bidirectional linkage: skill_run row should point back at automation_run.
    cursor = await conn.execute(
        "SELECT automation_run_id, status FROM skill_run WHERE id = ?",
        (skill_run_id_val,),
    )
    sr = await cursor.fetchone()
    assert sr is not None, "skill_run row not persisted"
    assert sr[1] == "succeeded", f"skill_run.status={sr[1]!r}"
    cursor = await conn.execute(
        "SELECT id FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    automation_run_row_id = (await cursor.fetchone())[0]
    assert sr[0] == automation_run_row_id, (
        "skill_run.automation_run_id must equal the automation_run.id"
    )

    # Alert dispatched via NotificationService -> FakeDonnaBot.
    assert len(runtime.fake_bot.sends) == sends_before + 1
    kind, target, content = runtime.fake_bot.sends[-1]
    assert kind == "channel"
    assert target == "tasks"
    assert content, "alert content must be non-empty"
