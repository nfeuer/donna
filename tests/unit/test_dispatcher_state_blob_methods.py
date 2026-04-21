"""Direct unit tests for AutomationDispatcher._query_state_blob + _update_state_blob."""
from __future__ import annotations

import json

import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_query_state_blob_returns_none_for_null(tmp_path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE automation (id TEXT PRIMARY KEY, state_blob TEXT)"
        )
        await conn.execute("INSERT INTO automation VALUES ('a-1', NULL)")
        await conn.commit()

        from donna.automations.dispatcher import AutomationDispatcher

        disp = object.__new__(AutomationDispatcher)
        disp._conn = conn

        result = await disp._query_state_blob(automation_id="a-1")
        assert result is None


@pytest.mark.asyncio
async def test_update_and_query_round_trip(tmp_path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE automation (id TEXT PRIMARY KEY, state_blob TEXT)"
        )
        await conn.execute("INSERT INTO automation VALUES ('a-1', NULL)")
        await conn.commit()

        from donna.automations.dispatcher import AutomationDispatcher

        disp = object.__new__(AutomationDispatcher)
        disp._conn = conn

        await disp._update_state_blob(
            automation_id="a-1", state_blob={"counter": 5, "name": "x"},
        )
        loaded = await disp._query_state_blob(automation_id="a-1")
        assert loaded == {"counter": 5, "name": "x"}


@pytest.mark.asyncio
async def test_query_state_blob_tolerates_corrupt_json(tmp_path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE automation (id TEXT PRIMARY KEY, state_blob TEXT)"
        )
        await conn.execute("INSERT INTO automation VALUES ('a-1', '{not valid}')")
        await conn.commit()

        from donna.automations.dispatcher import AutomationDispatcher

        disp = object.__new__(AutomationDispatcher)
        disp._conn = conn

        result = await disp._query_state_blob(automation_id="a-1")
        assert result is None
