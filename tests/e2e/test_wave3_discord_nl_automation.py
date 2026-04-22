"""AS-W3.1 — Discord NL 'watch URL ...' → confirmation → automation → alert DM.

End-to-end flow:

1. User DMs "watch https://cos.com/shirt daily for size L under $100"
2. DiscordIntentDispatcher calls ChallengerAgent (LLM), which returns an
   automation match at capability=product_watch with high confidence.
3. Dispatcher returns ``kind=automation_confirmation_needed`` with a
   DraftAutomation.
4. Approve via AutomationCreationPath → automation row persisted.
5. Scheduler runs once — product_watch skill is seeded at state=sandbox, so
   AutomationDispatcher takes the claude_native path. Claude (faked) returns
   an output with triggers_alert=true.
6. automation_run is written with status=succeeded + alert_sent=1, and the
   FakeDonnaBot records the DM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

import pytest


@dataclass
class _Msg:
    content: str
    author_id: str = "nick"
    thread_id: int | None = None


@pytest.mark.asyncio
async def test_high_confidence_nl_automation_creation_and_first_run(runtime) -> None:
    # Sanity: product_watch capability + sandbox skill are seeded by migration.
    conn = runtime.db.connection
    cursor = await conn.execute(
        "SELECT state FROM skill WHERE capability_name = 'product_watch'"
    )
    assert (await cursor.fetchone())[0] == "sandbox"

    # 1. Canned LLM responses.
    # ChallengerAgent posts task_type="challenge_task" through the router;
    # FakeRouter routes non-chat/non-skill_validation task_types to FakeClaude.
    runtime.fake_claude.canned["challenge_task"] = {
        "intent_kind": "automation",
        "capability_name": "product_watch",
        "match_score": 0.9,
        "confidence": 0.92,
        "extracted_inputs": {
            "url": "https://cos.com/shirt",
            "required_size": "L",
            "max_price_usd": 100,
        },
        "schedule": {"cron": "0 12 * * *", "human_readable": "daily at noon"},
        # Dispatcher persists alert_conditions verbatim; AlertEvaluator expects
        # the DSL shape (all_of/any_of/{field,op,value}), so the LLM is
        # instructed to return that form.
        "alert_conditions": {
            "all_of": [{"field": "triggers_alert", "op": "==", "value": True}],
        },
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }

    # 2. Dispatch the message.
    result = await runtime.intent_dispatcher.dispatch(
        _Msg(content="watch https://cos.com/shirt daily for size L under $100")
    )

    assert result.kind == "automation_confirmation_needed"
    assert result.draft_automation is not None
    draft = result.draft_automation
    assert draft.capability_name == "product_watch"
    assert draft.inputs["url"] == "https://cos.com/shirt"
    assert draft.target_cadence_cron == "0 12 * * *"
    # Sandbox floor for product_watch (from automations.yaml) is 12h.
    # Daily (12h target) is >= the floor so active stays at daily.
    assert draft.active_cadence_cron == "0 12 * * *"

    # 3. Approve → row exists.
    automation_id = await runtime.creation_path.approve(draft, name="watch shirt")
    assert automation_id is not None

    row = await runtime.automation_repo.get(automation_id)
    assert row is not None
    assert row.capability_name == "product_watch"
    assert row.status == "active"
    assert row.active_cadence_cron == "0 12 * * *"
    assert row.target_cadence_cron == "0 12 * * *"

    # 4. Force next_run_at to the past so the scheduler dispatches this tick.
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    await runtime.automation_repo.update_fields(
        automation_id, next_run_at=now - timedelta(minutes=5),
    )

    # Canned claude_native output for the dispatch step (task_type=capability).
    runtime.fake_claude.canned["product_watch"] = {
        "ok": True,
        "price_usd": 79.0,
        "currency": "USD",
        "in_stock": True,
        "size_available": True,
        "triggers_alert": True,
        "title": "Blue Shirt",
    }

    sends_before = len(runtime.fake_bot.sends)
    await runtime.automation_scheduler.run_once()

    # 5. Run + alert assertions.
    cursor = await conn.execute(
        "SELECT status, alert_sent, execution_path FROM automation_run "
        "WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    status, alert_sent, exec_path = rows[0]
    assert status == "succeeded"
    assert alert_sent == 1
    # Skill is at sandbox → dispatcher uses claude_native path.
    assert exec_path == "claude_native"

    assert len(runtime.fake_bot.sends) > sends_before
    # Alert went via NotificationService → FakeDonnaBot.
    last = runtime.fake_bot.sends[-1]
    assert last[2]  # non-empty content
