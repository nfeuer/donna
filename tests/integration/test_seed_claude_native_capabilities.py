"""Verify seed_claude_native_capabilities migration inserts the four rows."""
from __future__ import annotations

import pytest

from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.mark.asyncio
async def test_claude_native_capabilities_seeded(
    tmp_path, state_machine_config,
) -> None:
    db_path = tmp_path / "test.db"
    db = Database(str(db_path), StateMachine(state_machine_config))
    await db.connect()
    await db.run_migrations()

    expected = {"generate_digest", "prep_research", "task_decompose", "extract_preferences"}
    cursor = await db.connection.execute(
        "SELECT name FROM capability WHERE name IN "
        "('generate_digest','prep_research','task_decompose','extract_preferences')"
    )
    rows = await cursor.fetchall()
    seeded = {r[0] for r in rows}
    assert seeded == expected, f"missing: {expected - seeded}"
    await db.close()
