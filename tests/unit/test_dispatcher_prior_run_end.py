"""Tests: AutomationDispatcher injects prior_run_end into skill inputs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest


async def _make_empty_conn() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(
        "CREATE TABLE automation_run (id TEXT PRIMARY KEY, automation_id TEXT, "
        "status TEXT, finished_at TEXT)"
    )
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_first_ever_run_injects_null_prior_run_end():
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    got = await dispatcher._query_prior_run_end(automation_id=automation_id)
    assert got is None
    await conn.close()


@pytest.mark.asyncio
async def test_second_run_returns_prior_end_time():
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    prior = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, finished_at) "
        "VALUES (?, ?, 'succeeded', ?)",
        (str(uuid.uuid4()), automation_id, prior),
    )
    await conn.commit()

    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn
    got = await dispatcher._query_prior_run_end(automation_id=automation_id)
    assert got == prior
    await conn.close()


@pytest.mark.asyncio
async def test_failed_prior_run_ignored():
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    ok_time = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    failed_time = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, finished_at) "
        "VALUES (?, ?, 'succeeded', ?)",
        (str(uuid.uuid4()), automation_id, ok_time),
    )
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, finished_at) "
        "VALUES (?, ?, 'failed', ?)",
        (str(uuid.uuid4()), automation_id, failed_time),
    )
    await conn.commit()

    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn
    got = await dispatcher._query_prior_run_end(automation_id=automation_id)
    assert got == ok_time  # latest succeeded run, not latest-overall
    await conn.close()


@pytest.mark.asyncio
async def test_execute_skill_injects_prior_run_end_into_inputs():
    """Verify the call to executor.execute carries prior_run_end in inputs."""
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_empty_conn()
    automation_id = str(uuid.uuid4())
    prior = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO automation_run (id, automation_id, status, finished_at) "
        "VALUES (?, ?, 'succeeded', ?)",
        (str(uuid.uuid4()), automation_id, prior),
    )
    await conn.commit()

    # Minimal Automation stub.
    automation = MagicMock()
    automation.id = automation_id
    automation.capability_name = "news_check"
    automation.inputs = {"url": "x"}
    automation.user_id = "u1"

    # Skill + version rows so _execute_skill's SELECTs don't fail.
    await conn.execute(
        "CREATE TABLE skill (id TEXT PRIMARY KEY, capability_name TEXT, "
        "current_version_id TEXT, state TEXT, requires_human_gate INT, "
        "baseline_agreement REAL, created_at TEXT, updated_at TEXT)"
    )
    await conn.execute(
        "CREATE TABLE skill_version (id TEXT PRIMARY KEY, skill_id TEXT, "
        "version_number INT, yaml_backbone TEXT, step_content TEXT, "
        "output_schemas TEXT, created_by TEXT, changelog TEXT, created_at TEXT)"
    )
    skill_id = str(uuid.uuid4()); vid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO skill VALUES (?, ?, ?, 'sandbox', 0, 0.0, ?, ?)",
        (skill_id, "news_check", vid, prior, prior),
    )
    await conn.execute(
        "INSERT INTO skill_version VALUES (?, ?, 1, 'yaml', '{}', '{}', 'seed', '', ?)",
        (vid, skill_id, prior),
    )
    await conn.commit()

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        final_output={"ok": True}, total_cost_usd=0.0, status="succeeded",
        run_id=None, error=None, escalation_reason=None,
    ))
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    await dispatcher._execute_skill(executor, automation, automation_run_id=None)

    call_kwargs = executor.execute.call_args.kwargs
    assert call_kwargs["inputs"]["prior_run_end"] == prior
    assert call_kwargs["inputs"]["url"] == "x"
    await conn.close()
