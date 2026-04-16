"""Tests for /admin/automations REST routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from fastapi import HTTPException

from donna.api.routes.automations import (
    CreateAutomationRequest,
    UpdateAutomationRequest,
    create_automation,
    delete_automation,
    get_automation,
    get_runs,
    list_automations,
    pause_automation,
    resume_automation,
    run_now,
    update_automation,
)
from donna.automations.repository import AutomationRepository


SCHEMA = """
CREATE TABLE capability (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    description TEXT, input_schema TEXT, trigger_type TEXT,
    status TEXT NOT NULL, created_at TEXT NOT NULL,
    created_by TEXT NOT NULL, embedding BLOB
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
"""


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "t.db"))
    await conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO capability VALUES (?, 'product_watch', 'cap', '{}', "
        "'on_schedule', 'active', ?, 'seed', NULL)",
        ("c1", now),
    )
    await conn.commit()
    yield conn
    await conn.close()


def _make_request(conn, dispatcher=None):
    request = MagicMock()
    request.app.state.db.connection = conn
    request.app.state.automation_dispatcher = dispatcher
    # No cron_calculator on state → routes fall back to CronScheduleCalculator()
    del request.app.state.cron_calculator
    return request


# ---------------------------------------------------------------------------
# Helper: seed an automation row
# ---------------------------------------------------------------------------

async def _seed_automation(conn, *, name="Watch shirt", status="active", schedule="0 12 * * *"):
    repo = AutomationRepository(conn)
    auto_id = await repo.create(
        user_id="nick",
        name=name,
        description=None,
        capability_name="product_watch",
        inputs={"url": "https://example.com"},
        trigger_type="on_schedule",
        schedule=schedule,
        alert_conditions={},
        alert_channels=[],
        max_cost_per_run_usd=None,
        min_interval_seconds=300,
        created_via="dashboard",
    )
    if status != "active":
        await repo.set_status(auto_id, status)
    return auto_id


async def _seed_run(conn, automation_id: str):
    repo = AutomationRepository(conn)
    run_id = await repo.insert_run(
        automation_id=automation_id,
        started_at=datetime.now(timezone.utc),
        execution_path="claude_native",
    )
    return run_id


# ===========================================================================
# 1. POST /automations — create happy path
# ===========================================================================

async def test_post_create_returns_automation_id(db):
    request = _make_request(db)
    body = CreateAutomationRequest(
        user_id="nick",
        name="Watch shirt",
        capability_name="product_watch",
        inputs={"url": "https://example.com"},
        trigger_type="on_schedule",
        schedule="0 12 * * *",
    )
    response = await create_automation(body=body, request=request)
    assert "id" in response
    assert response["name"] == "Watch shirt"
    assert response["next_run_at"] is not None


# ===========================================================================
# 2. POST /automations — unknown capability → 400
# ===========================================================================

async def test_post_create_rejects_missing_capability(db):
    request = _make_request(db)
    body = CreateAutomationRequest(
        user_id="nick",
        name="Watch shirt",
        capability_name="nonexistent_cap",
        inputs={},
        trigger_type="on_manual",
    )
    with pytest.raises(HTTPException) as exc_info:
        await create_automation(body=body, request=request)
    assert exc_info.value.status_code == 400
    assert "nonexistent_cap" in exc_info.value.detail


# ===========================================================================
# 3. POST /automations — bad cron → 400
# ===========================================================================

async def test_post_create_rejects_invalid_cron(db):
    request = _make_request(db)
    body = CreateAutomationRequest(
        user_id="nick",
        name="Watch shirt",
        capability_name="product_watch",
        inputs={},
        trigger_type="on_schedule",
        schedule="not a cron",
    )
    with pytest.raises(HTTPException) as exc_info:
        await create_automation(body=body, request=request)
    assert exc_info.value.status_code == 400


# ===========================================================================
# 4. GET /automations — default filter is active
# ===========================================================================

async def test_get_list_returns_active_by_default(db):
    request = _make_request(db)
    await _seed_automation(db, name="Active one", status="active")
    await _seed_automation(db, name="Paused one", status="paused")

    response = await list_automations(
        request=request, status="active", capability_name=None, limit=100, offset=0
    )
    names = [a["name"] for a in response["automations"]]
    assert "Active one" in names
    assert "Paused one" not in names


# ===========================================================================
# 5. GET /automations — filter by capability_name
# ===========================================================================

async def test_get_list_filters_by_capability_name(db):
    # Seed a second capability
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO capability VALUES (?, 'price_alert', 'cap2', '{}', "
        "'on_schedule', 'active', ?, 'seed', NULL)",
        ("c2", now),
    )
    await db.commit()

    repo = AutomationRepository(db)
    await repo.create(
        user_id="nick", name="Price job",
        description=None, capability_name="price_alert",
        inputs={}, trigger_type="on_manual", schedule=None,
        alert_conditions={}, alert_channels=[],
        max_cost_per_run_usd=None, min_interval_seconds=300,
        created_via="dashboard",
    )
    await _seed_automation(db, name="Watch job")

    request = _make_request(db)
    response = await list_automations(
        request=request, status="active",
        capability_name="price_alert", limit=100, offset=0,
    )
    assert response["count"] == 1
    assert response["automations"][0]["name"] == "Price job"


# ===========================================================================
# 6. GET /automations/{id} — single detail
# ===========================================================================

async def test_get_single_returns_detail(db):
    auto_id = await _seed_automation(db)
    request = _make_request(db)
    response = await get_automation(automation_id=auto_id, request=request)
    assert response["id"] == auto_id
    assert response["name"] == "Watch shirt"


# ===========================================================================
# 7. GET /automations/{id} — 404 for unknown id
# ===========================================================================

async def test_get_single_404(db):
    request = _make_request(db)
    with pytest.raises(HTTPException) as exc_info:
        await get_automation(automation_id="does-not-exist", request=request)
    assert exc_info.value.status_code == 404


# ===========================================================================
# 8. PATCH /automations/{id} — update fields
# ===========================================================================

async def test_patch_updates_fields(db):
    auto_id = await _seed_automation(db)
    request = _make_request(db)
    body = UpdateAutomationRequest(name="Updated name", inputs={"url": "https://new.com"})
    response = await update_automation(automation_id=auto_id, body=body, request=request)
    assert response["name"] == "Updated name"
    assert response["inputs"]["url"] == "https://new.com"


# ===========================================================================
# 9. PATCH /automations/{id} — schedule change recomputes next_run_at
# ===========================================================================

async def test_patch_recomputes_next_run_when_schedule_changes(db):
    auto_id = await _seed_automation(db, schedule="0 6 * * *")
    request = _make_request(db)

    # Fetch current next_run_at
    before = await get_automation(automation_id=auto_id, request=request)
    old_next_run = before["next_run_at"]

    body = UpdateAutomationRequest(schedule="0 18 * * *")
    response = await update_automation(automation_id=auto_id, body=body, request=request)

    assert response["schedule"] == "0 18 * * *"
    assert response["next_run_at"] is not None
    # The new next_run_at should differ from the old one (different hour)
    assert response["next_run_at"] != old_next_run


# ===========================================================================
# 10. POST /automations/{id}/pause — sets status=paused
# ===========================================================================

async def test_post_pause_sets_status(db):
    auto_id = await _seed_automation(db, status="active")
    request = _make_request(db)
    response = await pause_automation(automation_id=auto_id, request=request)
    assert response["status"] == "paused"


# ===========================================================================
# 11. POST /automations/{id}/resume — sets status=active and recomputes schedule
# ===========================================================================

async def test_post_resume_sets_status_and_schedule(db):
    auto_id = await _seed_automation(db, status="paused")
    request = _make_request(db)
    response = await resume_automation(automation_id=auto_id, request=request)
    assert response["status"] == "active"
    assert response["next_run_at"] is not None


# ===========================================================================
# 12. DELETE /automations/{id} — soft delete
# ===========================================================================

async def test_delete_soft_deletes(db):
    auto_id = await _seed_automation(db)
    request = _make_request(db)
    response = await delete_automation(automation_id=auto_id, request=request)
    assert response["status"] == "deleted"

    # Verify row is still in DB with status=deleted
    repo = AutomationRepository(db)
    row = await repo.get(auto_id)
    assert row is not None
    assert row.status == "deleted"


# ===========================================================================
# 13. POST /automations/{id}/run-now — dispatches immediately
# ===========================================================================

async def test_post_run_now_dispatches_immediately(db):
    auto_id = await _seed_automation(db)

    # Build a fake DispatchReport-shaped object
    fake_report = MagicMock()
    fake_report.automation_id = auto_id
    fake_report.run_id = "run-xyz"
    fake_report.outcome = "succeeded"
    fake_report.alert_sent = False
    fake_report.error = None

    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(return_value=fake_report)

    request = _make_request(db, dispatcher=dispatcher)
    response = await run_now(automation_id=auto_id, request=request)

    dispatcher.dispatch.assert_called_once()
    assert response["outcome"] == "succeeded"
    assert response["run_id"] == "run-xyz"


async def test_post_run_now_503_when_no_dispatcher(db):
    auto_id = await _seed_automation(db)
    request = _make_request(db, dispatcher=None)
    with pytest.raises(HTTPException) as exc_info:
        await run_now(automation_id=auto_id, request=request)
    assert exc_info.value.status_code == 503


# ===========================================================================
# 14. GET /automations/{id}/runs — run history
# ===========================================================================

async def test_get_runs_returns_history(db):
    auto_id = await _seed_automation(db)
    await _seed_run(db, auto_id)
    await _seed_run(db, auto_id)

    request = _make_request(db)
    response = await get_runs(
        automation_id=auto_id, request=request, limit=50, offset=0
    )
    assert response["count"] == 2
    assert all(r["automation_id"] == auto_id for r in response["runs"])
