"""Wave 4 cross-capability — one tick, three automations, no cross-talk.

AS-W4.7 — product_watch + news_check + email_triage all due at the same time.
Asserts per-automation isolation (distinct automation_run rows, correct
alert_sent values, no shared-state bleed between capabilities).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------


async def _seed_automation(
    runtime,
    *,
    capability: str,
    user_id: str,
    inputs: dict,
    schedule: str,
    name: str | None = None,
) -> str:
    """Insert a due automation row and return its id."""
    conn = runtime.db.connection
    now = datetime.now(tz=UTC)
    past = (now - timedelta(minutes=5)).isoformat()
    automation_id = str(uuid.uuid4())
    row_name = name or f"Test {capability}"
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, ?, ?, NULL, ?, ?, "
        "'on_schedule', ?, ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            user_id,
            row_name,
            capability,
            json.dumps(inputs),
            schedule,
            json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    await conn.commit()
    return automation_id


async def _query_automation_runs(runtime, *, user_id: str) -> list[dict]:
    """Return all automation_run rows for automations owned by user_id.

    Joins automation_run with automation so each row includes capability_name
    and automation_id, matching the assertions in AS-W4.7.
    """
    conn = runtime.db.connection
    cursor = await conn.execute(
        "SELECT ar.automation_id, ar.status, ar.alert_sent, "
        "       ar.execution_path, a.capability_name "
        "FROM automation_run ar "
        "JOIN automation a ON a.id = ar.automation_id "
        "WHERE a.user_id = ?",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "automation_id": row[0],
            "status": row[1],
            "alert_sent": row[2],
            "execution_path": row[3],
            "capability_name": row[4],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# AS-W4.7 — single tick, three capabilities, isolated runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_stack_single_tick_isolates_runs(runtime) -> None:
    """AS-W4.7 — product_watch + news_check + email_triage in one tick.

    Assert per-automation isolation and alert dispatch for only alerting
    automations.  All three skills are in sandbox state → claude_native path.
    """
    # ---- 1. Canned FakeClaude responses for all three capabilities. ----------
    runtime.fake_claude.canned["product_watch"] = {
        "ok": True,
        "in_stock": True,
        "size_available": True,
        "triggers_alert": True,
        "price_usd": 79.0,
        "message": "Patagonia jacket: $79 in size L, in stock.",
    }
    runtime.fake_claude.canned["news_check"] = {
        "ok": True,
        "triggers_alert": True,
        "message": (
            "• Alignment paper — https://example.com/a1\n"
            "• Policy brief — https://example.com/a2"
        ),
        "meta": {
            "item_count": 2,
            "action_required_count": 2,
            "source_feed": "AI Safety Daily",
        },
    }
    runtime.fake_claude.canned["email_triage"] = {
        "ok": True,
        "triggers_alert": False,
        "message": None,
        "meta": {
            "item_count": 0,
            "action_required_count": 0,
            "snippet_scanned_count": 0,
            "body_fetched_count": 0,
        },
    }

    # ---- 2. Seed three automations, all due. ---------------------------------
    prod_id = await _seed_automation(
        runtime,
        capability="product_watch",
        user_id="nick",
        inputs={
            "url": "https://shop.example.com/jacket",
            "max_price_usd": 100,
            "required_size": "L",
        },
        schedule="0 */12 * * *",
        name="Full-stack product_watch",
    )
    news_id = await _seed_automation(
        runtime,
        capability="news_check",
        user_id="nick",
        inputs={
            "feed_urls": ["https://example.com/feed"],
            "topics": ["AI safety"],
        },
        schedule="0 */12 * * *",
        name="Full-stack news_check",
    )
    mail_id = await _seed_automation(
        runtime,
        capability="email_triage",
        user_id="nick",
        inputs={
            "senders": ["nobody@x.com"],
            "query_extras": None,  # explicit null — avoids StrictUndefined
        },
        schedule="0 */12 * * *",
        name="Full-stack email_triage",
    )

    # ---- 3. Run a single scheduler tick (processes all due automations). -----
    await runtime.automation_scheduler.run_once()

    # ---- 4. Assertions. ------------------------------------------------------
    runs = await _query_automation_runs(runtime, user_id="nick")
    assert len(runs) == 3, (
        f"expected 3 automation_run rows, got {len(runs)}: {runs}"
    )

    by_cap = {r["capability_name"]: r for r in runs}

    # product_watch — alert fires.
    assert by_cap["product_watch"]["status"] == "succeeded", (
        f"product_watch status={by_cap['product_watch']['status']!r}"
    )
    assert by_cap["product_watch"]["alert_sent"] == 1, (
        "product_watch should have alert_sent=1 (triggers_alert=True)"
    )

    # news_check — alert fires.
    assert by_cap["news_check"]["status"] == "succeeded", (
        f"news_check status={by_cap['news_check']['status']!r}"
    )
    assert by_cap["news_check"]["alert_sent"] == 1, (
        "news_check should have alert_sent=1 (triggers_alert=True)"
    )

    # email_triage — quiet run, no alert.
    assert by_cap["email_triage"]["status"] == "succeeded", (
        f"email_triage status={by_cap['email_triage']['status']!r}"
    )
    assert by_cap["email_triage"]["alert_sent"] == 0, (
        "email_triage should have alert_sent=0 (triggers_alert=False)"
    )

    # All three automation_ids are distinct (no row-merging).
    automation_ids = {r["automation_id"] for r in runs}
    assert len(automation_ids) == 3, (
        f"expected 3 distinct automation_ids, got {len(automation_ids)}: {automation_ids}"
    )
    assert prod_id in automation_ids
    assert news_id in automation_ids
    assert mail_id in automation_ids

    # Exactly 2 DMs sent — product_watch + news_check both alert; email_triage quiet.
    assert len(runtime.fake_bot.sends) == 2, (
        f"expected 2 bot sends (product_watch + news_check); "
        f"got {len(runtime.fake_bot.sends)}: {runtime.fake_bot.sends}"
    )
    for kind, target, content in runtime.fake_bot.sends:
        assert kind == "channel", f"unexpected send kind {kind!r}"
        assert target == "tasks", f"unexpected channel {target!r}"
        assert content, "alert content must be non-empty"
