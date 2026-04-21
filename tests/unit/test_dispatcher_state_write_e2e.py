"""End-to-end test: AutomationDispatcher state_write dispatch path.

F-W4-D: verifies that keys listed in yaml_backbone.state_write are extracted
from the executor's final_output and persisted via _update_state_blob.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest


async def _make_conn() -> aiosqlite.Connection:
    """Return an in-memory DB with the tables _execute_skill needs."""
    conn = await aiosqlite.connect(":memory:")
    now = datetime.now(timezone.utc).isoformat()

    await conn.execute(
        "CREATE TABLE automation ("
        "id TEXT PRIMARY KEY, state_blob TEXT)"
    )
    await conn.execute(
        "CREATE TABLE automation_run ("
        "id TEXT PRIMARY KEY, automation_id TEXT, "
        "status TEXT, finished_at TEXT)"
    )
    await conn.execute(
        "CREATE TABLE skill ("
        "id TEXT PRIMARY KEY, capability_name TEXT, "
        "current_version_id TEXT, state TEXT, requires_human_gate INT, "
        "baseline_agreement REAL, created_at TEXT, updated_at TEXT)"
    )
    await conn.execute(
        "CREATE TABLE skill_version ("
        "id TEXT PRIMARY KEY, skill_id TEXT, "
        "version_number INT, yaml_backbone TEXT, step_content TEXT, "
        "output_schemas TEXT, created_by TEXT, changelog TEXT, created_at TEXT)"
    )
    await conn.commit()
    return conn


async def _insert_skill_and_version(
    conn: aiosqlite.Connection,
    *,
    capability_name: str,
    yaml_backbone: str,
) -> tuple[str, str]:
    """Insert a skill + version row; return (skill_id, version_id)."""
    now = datetime.now(timezone.utc).isoformat()
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO skill VALUES (?, ?, ?, 'shadow_primary', 0, 0.0, ?, ?)",
        (skill_id, capability_name, version_id, now, now),
    )
    await conn.execute(
        "INSERT INTO skill_version VALUES (?, ?, 1, ?, '{}', '{}', 'seed', '', ?)",
        (version_id, skill_id, yaml_backbone, now),
    )
    await conn.commit()
    return skill_id, version_id


def _make_automation(automation_id: str, capability_name: str) -> MagicMock:
    automation = MagicMock()
    automation.id = automation_id
    automation.capability_name = capability_name
    automation.inputs = {}
    automation.user_id = "u1"
    return automation


def _make_executor(final_output: dict) -> MagicMock:
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        final_output=final_output,
        total_cost_usd=0.0,
        status="succeeded",
        run_id=None,
        error=None,
        escalation_reason=None,
    ))
    return executor


@pytest.mark.asyncio
async def test_state_write_persists_key_on_first_run() -> None:
    """First call: state_blob is NULL; after run with counter=7 it becomes {counter:7}."""
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_conn()
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, state_blob) VALUES (?, NULL)",
        (automation_id,),
    )
    await conn.commit()

    await _insert_skill_and_version(
        conn,
        capability_name="counter_cap",
        yaml_backbone="state_write:\n  - counter\n",
    )

    automation = _make_automation(automation_id, "counter_cap")
    executor = _make_executor({"counter": 7, "ignored_key": "x"})

    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    await dispatcher._execute_skill(executor, automation, automation_run_id=None)

    result = await dispatcher._query_state_blob(automation_id=automation_id)
    assert result == {"counter": 7}, f"Expected {{counter: 7}}, got {result!r}"

    await conn.close()


@pytest.mark.asyncio
async def test_state_write_updates_key_on_second_run() -> None:
    """Second run with counter=8 must overwrite the prior value."""
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_conn()
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, state_blob) VALUES (?, NULL)",
        (automation_id,),
    )
    await conn.commit()

    await _insert_skill_and_version(
        conn,
        capability_name="counter_cap",
        yaml_backbone="state_write:\n  - counter\n",
    )

    automation = _make_automation(automation_id, "counter_cap")
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    # First run.
    await dispatcher._execute_skill(
        _make_executor({"counter": 7}), automation, automation_run_id=None
    )
    first = await dispatcher._query_state_blob(automation_id=automation_id)
    assert first == {"counter": 7}

    # Second run — value changes.
    await dispatcher._execute_skill(
        _make_executor({"counter": 8}), automation, automation_run_id=None
    )
    second = await dispatcher._query_state_blob(automation_id=automation_id)
    assert second == {"counter": 8}, f"Expected {{counter: 8}}, got {second!r}"

    await conn.close()


@pytest.mark.asyncio
async def test_state_write_no_update_when_value_unchanged() -> None:
    """If the key value didn't change, _update_state_blob must NOT be called."""
    from unittest.mock import patch
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_conn()
    automation_id = str(uuid.uuid4())
    import json
    await conn.execute(
        "INSERT INTO automation (id, state_blob) VALUES (?, ?)",
        (automation_id, json.dumps({"counter": 7})),
    )
    await conn.commit()

    await _insert_skill_and_version(
        conn,
        capability_name="counter_cap",
        yaml_backbone="state_write:\n  - counter\n",
    )

    automation = _make_automation(automation_id, "counter_cap")
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    with patch.object(dispatcher, "_update_state_blob", new=AsyncMock()) as mock_update:
        await dispatcher._execute_skill(
            _make_executor({"counter": 7}), automation, automation_run_id=None
        )
        mock_update.assert_not_called()

    await conn.close()


@pytest.mark.asyncio
async def test_state_write_empty_list_skips_update() -> None:
    """No state_write keys in backbone — state_blob stays NULL."""
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_conn()
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, state_blob) VALUES (?, NULL)",
        (automation_id,),
    )
    await conn.commit()

    await _insert_skill_and_version(
        conn,
        capability_name="no_state_cap",
        yaml_backbone="step_count: 1\n",  # no state_write key
    )

    automation = _make_automation(automation_id, "no_state_cap")
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    await dispatcher._execute_skill(
        _make_executor({"counter": 99}), automation, automation_run_id=None
    )

    result = await dispatcher._query_state_blob(automation_id=automation_id)
    assert result is None

    await conn.close()


@pytest.mark.asyncio
async def test_state_write_key_absent_from_output_is_ignored() -> None:
    """Key in state_write but absent from final_output — state_blob untouched."""
    from donna.automations.dispatcher import AutomationDispatcher

    conn = await _make_conn()
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, state_blob) VALUES (?, NULL)",
        (automation_id,),
    )
    await conn.commit()

    await _insert_skill_and_version(
        conn,
        capability_name="missing_key_cap",
        yaml_backbone="state_write:\n  - counter\n",
    )

    automation = _make_automation(automation_id, "missing_key_cap")
    dispatcher = AutomationDispatcher.__new__(AutomationDispatcher)
    dispatcher._conn = conn

    # Output does NOT contain 'counter'.
    await dispatcher._execute_skill(
        _make_executor({"other": "value"}), automation, automation_run_id=None
    )

    result = await dispatcher._query_state_blob(automation_id=automation_id)
    assert result is None

    await conn.close()
