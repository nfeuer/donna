"""Unit tests for DashboardSettingResolver."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_repository import EscalationRepository

_SCHEMA = """
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "ds.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def resolver(conn: aiosqlite.Connection) -> DashboardSettingResolver:
    return DashboardSettingResolver(EscalationRepository(conn))


async def test_returns_default_when_missing(
    resolver: DashboardSettingResolver,
) -> None:
    assert await resolver.get("absent", True) is True
    assert await resolver.get("absent", 42) == 42


async def test_returns_stored_when_present(
    resolver: DashboardSettingResolver,
    conn: aiosqlite.Connection,
) -> None:
    repo = EscalationRepository(conn)
    await repo.upsert_dashboard_setting("manual_escalation.enabled", False)
    assert await resolver.get("manual_escalation.enabled", True) is False


async def test_falls_back_on_type_mismatch(
    resolver: DashboardSettingResolver,
    conn: aiosqlite.Connection,
) -> None:
    repo = EscalationRepository(conn)
    # Store an int when the caller expects a bool — should fall back.
    # bool is a subclass of int in Python so we use a string default.
    await repo.upsert_dashboard_setting("xyz", 123)
    assert await resolver.get("xyz", "fallback") == "fallback"


async def test_stored_list_returns_list(
    resolver: DashboardSettingResolver,
    conn: aiosqlite.Connection,
) -> None:
    repo = EscalationRepository(conn)
    await repo.upsert_dashboard_setting("modes", ["chat", "claude_code"])
    result = await resolver.get("modes", ["pause"])
    assert result == ["chat", "claude_code"]
