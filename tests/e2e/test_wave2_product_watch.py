"""Wave 2 E2E: product_watch automation runs end-to-end through claude_native path."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

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

    now = datetime.now(tz=timezone.utc)
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
    now = datetime.now(tz=timezone.utc)
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
