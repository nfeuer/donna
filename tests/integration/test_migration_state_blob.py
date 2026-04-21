"""Verify add_automation_state_blob migration adds the column."""
from __future__ import annotations

import pytest

from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.mark.asyncio
async def test_state_blob_column_exists(tmp_path, state_machine_config) -> None:
    db_path = tmp_path / "test.db"
    state_machine = StateMachine(state_machine_config)
    db = Database(str(db_path), state_machine)
    await db.connect()
    await db.run_migrations()

    cursor = await db.connection.execute(
        "SELECT name FROM pragma_table_info('automation') WHERE name = 'state_blob'"
    )
    row = await cursor.fetchone()
    assert row is not None, "state_blob column missing from automation table"
    await db.close()
