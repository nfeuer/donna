"""Wave 4 E2E — email_triage: NL creation, body-fetch skip, capability guard.

Three acceptance scenarios from the Wave 4 spec:

AS-W4.4 — Seed an email_triage automation (claude_native / sandbox), tick
           the scheduler with a canned response that returns 2 action-required
           messages.  Assert automation_run.status=succeeded, alert_sent=1,
           FakeDonnaBot DM.

AS-W4.5 — Promote email_triage to shadow_primary.  Wire a stub SkillExecutor
           that patches gmail_search and gmail_get_message in a fresh
           ToolRegistry.  Canned FakeRouter responses for classify_snippets
           (0 candidates) → render_digest (triggers_alert=False).  Assert
           gmail_get_message invocation count = 0 and alert_sent = 0.

AS-W4.6 — AutomationCreationPath.approve() raises MissingToolError when
           gmail_search / gmail_get_message are absent from the ToolRegistry.
           No automation row written.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Canned Gmail mock responses
# ---------------------------------------------------------------------------

GMAIL_SEARCH_THREE_MSGS = {
    "ok": True,
    "messages": [
        {
            "id": "m1",
            "sender": "Jane <jane@x.com>",
            "subject": "Re: Q2 roadmap",
            "snippet": "Can you confirm timelines by Friday?",
            "internal_date": "2026-04-20T08:00:00+00:00",
        },
        {
            "id": "m2",
            "sender": "team@x.com",
            "subject": "Budget approval needed",
            "snippet": "Please approve the attached budget request.",
            "internal_date": "2026-04-20T06:00:00+00:00",
        },
        {
            "id": "m3",
            "sender": "Jane <jane@x.com>",
            "subject": "FYI conference recording",
            "snippet": "Here is the conference recording.",
            "internal_date": "2026-04-20T04:00:00+00:00",
        },
    ],
}

GMAIL_SEARCH_ZERO_CANDIDATES = {
    "ok": True,
    "messages": [
        {
            "id": "m4",
            "sender": "Jane <jane@x.com>",
            "subject": "FYI lunch schedule",
            "snippet": "Sharing the lunch schedule for next week.",
            "internal_date": "2026-04-20T09:00:00+00:00",
        },
    ],
}

# ---------------------------------------------------------------------------
# Shared insert helper
# ---------------------------------------------------------------------------


async def _insert_email_triage_automation(
    conn,
    automation_id: str,
    now: datetime,
    past: str,
    name: str = "Triage action-required emails",
    include_query_extras: bool = False,
) -> None:
    """Insert a due email_triage automation row into the test DB.

    ``include_query_extras=True`` adds ``query_extras: null`` to the inputs
    JSON so that the skill YAML's ``{% if inputs.query_extras %}`` template
    guard resolves cleanly under Jinja's StrictUndefined when running the
    real SkillExecutor (shadow_primary path).
    """
    inputs: dict = {"senders": ["jane@x.com", "team@x.com"]}
    if include_query_extras:
        inputs["query_extras"] = None
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', ?, NULL, 'email_triage', ?, "
        "'on_schedule', '0 */12 * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            name,
            json.dumps(inputs),
            json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# AS-W4.4 — NL creation → action-required digest (claude_native sandbox path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_triage_nl_creation_action_required_digest(runtime) -> None:
    """AS-W4.4 — search returns 3 msgs, digest DMs 2 action-required.

    Uses claude_native dispatch (sandbox state).  FakeClaude returns a
    pre-cooked digest-shape output for the email_triage task_type.
    Verifies automation_run.alert_sent=1 and at least one DM fired.
    """
    conn = runtime.db.connection

    # Verify the seed migration ran — email_triage capability + sandbox skill.
    cursor = await conn.execute(
        "SELECT state FROM skill WHERE capability_name = 'email_triage'"
    )
    row = await cursor.fetchone()
    assert row is not None, "email_triage skill should be seeded by migration"
    assert row[0] == "sandbox", f"expected sandbox, got {row[0]}"

    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(minutes=5)).isoformat()

    # Canned claude_native response — 2 action-required messages digested.
    runtime.fake_claude.canned["email_triage"] = {
        "ok": True,
        "triggers_alert": True,
        "message": (
            "2 action-required emails:\n"
            "- Re: Q2 roadmap (jane@x.com) — please confirm timelines by Friday\n"
            "- Budget approval needed (team@x.com) — approval deadline end of week"
        ),
        "meta": {
            "item_count": 3,
            "action_required_count": 2,
            "snippet_scanned_count": 3,
            "body_fetched_count": 2,
        },
    }

    automation_id = str(uuid.uuid4())
    await _insert_email_triage_automation(conn, automation_id, now, past)

    await runtime.automation_scheduler.run_once()

    # Assert automation_run recorded and alert fired.
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

    # Alert dispatched via NotificationService -> FakeDonnaBot.
    assert len(runtime.fake_bot.sends) >= 1, (
        f"expected at least 1 bot send; got {runtime.fake_bot.sends}"
    )
    kind, target, content = runtime.fake_bot.sends[-1]
    assert kind == "channel"
    assert target == "tasks"
    assert content, "alert content must be non-empty"


# ---------------------------------------------------------------------------
# AS-W4.5 — Zero candidates → gmail_get_message NOT invoked (shadow_primary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_triage_body_fetch_skipped_when_no_candidates(runtime) -> None:
    """AS-W4.5 — classify_snippets returns 0 candidates → fetch_bodies step
    runs 0 for_each iterations → gmail_get_message call count = 0.

    Uses the _WrapExecutor pattern from news_check AS-W4.3.  Promotes
    email_triage to shadow_primary, wires a real SkillExecutor with patched
    gmail_search + gmail_get_message tools (both tracked), canned FakeRouter
    responses for classify_snippets (empty) and render_digest (no alert).
    """
    from donna.skills.executor import SkillExecutor
    from donna.skills.run_persistence import SkillRunRepository
    from donna.skills.tool_registry import ToolRegistry
    from donna.tasks.db_models import SkillState

    conn = runtime.db.connection

    # --- 1. Locate seeded email_triage skill + promote via lifecycle. ----------
    cursor = await conn.execute(
        "SELECT id, current_version_id, state FROM skill "
        "WHERE capability_name = 'email_triage'"
    )
    skill_row = await cursor.fetchone()
    assert skill_row is not None, "email_triage skill should be seeded by migration"
    skill_id, version_id, initial_state = skill_row
    assert initial_state == "sandbox", f"expected sandbox, got {initial_state}"

    lifecycle = runtime.skill_bundle.lifecycle_manager
    await lifecycle.transition(
        skill_id=skill_id,
        to_state=SkillState.SHADOW_PRIMARY,
        reason="human_approval",
        actor="user",
        actor_id="test-nick",
        notes="AS-W4.5 e2e",
    )

    cursor = await conn.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    assert (await cursor.fetchone())[0] == "shadow_primary"

    # --- 2. Canned FakeRouter responses for the three LLM steps. ---------------
    # classify_snippets returns 0 candidates → fetch_bodies iterates nothing.
    runtime.fake_claude.canned["skill_step::email_triage::classify_snippets"] = {
        "candidates": [],
        "total_scanned": 1,
    }
    # classify_bodies still gets called (empty confirmed list is valid).
    runtime.fake_claude.canned["skill_step::email_triage::classify_bodies"] = {
        "confirmed": [],
        "rejected_ids": [],
        "body_fetched": False,
    }
    runtime.fake_claude.canned["skill_step::email_triage::render_digest"] = {
        "ok": True,
        "triggers_alert": False,
        "message": None,
        "meta": {
            "item_count": 1,
            "action_required_count": 0,
            "snippet_scanned_count": 1,
            "body_fetched_count": 0,
        },
    }

    # --- 3. Build a real SkillExecutor with instrumented tool mocks. ------------
    gmail_search_calls: list[dict] = []
    gmail_get_message_calls: list[dict] = []

    async def _fake_gmail_search(**kwargs):
        gmail_search_calls.append(kwargs)
        return GMAIL_SEARCH_ZERO_CANDIDATES

    async def _fake_gmail_get_message(**kwargs):
        gmail_get_message_calls.append(kwargs)
        return {"ok": True, "body_plain": "body text", "sender": "jane@x.com",
                "subject": "FYI", "internal_date": "2026-04-20T09:00:00+00:00",
                "body_html": None, "headers": {}}

    fake_tool_registry = ToolRegistry()
    fake_tool_registry.register("gmail_search", _fake_gmail_search)
    fake_tool_registry.register("gmail_get_message", _fake_gmail_get_message)

    run_repo = SkillRunRepository(conn)
    executor_calls: list[dict] = []

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

    # --- 4. Create a due email_triage automation. --------------------------------
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(minutes=5)).isoformat()
    automation_id = str(uuid.uuid4())
    await _insert_email_triage_automation(
        conn, automation_id, now, past,
        name="Skill-Path email_triage zero-candidates",
        include_query_extras=True,
    )

    sends_before = len(runtime.fake_bot.sends)

    # --- 5. Tick the scheduler. --------------------------------------------------
    await runtime.automation_scheduler.run_once()

    # --- 6. Assert skill path was taken. -----------------------------------------
    assert len(executor_calls) == 1, (
        f"expected 1 skill execution, got {len(executor_calls)}. "
        "If zero, dispatcher did NOT route to skill path at shadow_primary."
    )
    assert executor_calls[0]["capability"] == "email_triage"

    # Core assertion: gmail_get_message must NOT be called when no candidates.
    assert len(gmail_get_message_calls) == 0, (
        f"gmail_get_message should NOT be called when classify_snippets returns "
        f"0 candidates; got {len(gmail_get_message_calls)} calls: {gmail_get_message_calls}"
    )
    # gmail_search must have been called once (the search_messages step).
    assert len(gmail_search_calls) == 1, (
        f"gmail_search should be called once; got {len(gmail_search_calls)}"
    )

    # No alert since triggers_alert=False.
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
    assert alert_sent == 0, "no alert should fire when triggers_alert=False"

    # No new Discord sends.
    assert len(runtime.fake_bot.sends) == sends_before, (
        f"expected no new sends; got {runtime.fake_bot.sends[sends_before:]}"
    )


# ---------------------------------------------------------------------------
# AS-W4.6 — Gmail-not-connected guard rejects at approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_triage_guard_rejects_when_gmail_missing() -> None:
    """AS-W4.6 — AutomationCreationPath.approve() raises MissingToolError
    when gmail_search / gmail_get_message are not in the ToolRegistry.

    This test exercises the guard directly (not via scheduler tick) — the
    approval step is the integration point, and we want to assert the error
    is raised before any DB write.
    """
    from donna.automations.creation_flow import AutomationCreationPath, MissingToolError
    from donna.skills.tool_registry import ToolRegistry
    from donna.orchestrator.discord_intent_dispatcher import DraftAutomation

    # Registry with non-gmail tools only.
    reg = ToolRegistry()
    reg.register("web_fetch", AsyncMock())
    reg.register("rss_fetch", AsyncMock())
    # No gmail_search / gmail_get_message registered.

    # Lookup returns what the email_triage skill YAML declares as required tools.
    lookup = AsyncMock(return_value=["gmail_search", "gmail_get_message"])
    repo = AsyncMock()

    path = AutomationCreationPath(
        repository=repo,
        tool_registry=reg,
        capability_tool_lookup=lookup,
    )

    draft = DraftAutomation(
        user_id="u1",
        capability_name="email_triage",
        inputs={"senders": ["jane@x.com", "team@x.com"]},
        schedule_cron="0 */12 * * *",
        target_cadence_cron="0 */12 * * *",
        active_cadence_cron="0 */12 * * *",
        alert_conditions={},
        schedule_human="every 12 hours",
    )

    with pytest.raises(MissingToolError) as ei:
        await path.approve(draft, name="triage-jane")

    err_str = str(ei.value)
    assert "gmail_search" in err_str, (
        f"MissingToolError should mention gmail_search; got: {err_str!r}"
    )
    assert "gmail_get_message" in err_str, (
        f"MissingToolError should mention gmail_get_message; got: {err_str!r}"
    )

    # No automation row should have been written.
    repo.create.assert_not_called()
