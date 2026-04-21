"""Tests for CapabilityToolRegistryCheck."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.capabilities.capability_tool_check import (
    CapabilityToolConfigError,
    CapabilityToolRegistryCheck,
)
from donna.skills.tool_registry import ToolRegistry


def _conn_returning(rows: list[tuple[str, str | None]]) -> MagicMock:
    conn = MagicMock()
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=rows)
    conn.execute = AsyncMock(return_value=cursor)
    return conn


async def _noop(**_: object) -> dict:
    return {"ok": True}


def _registry_with(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(n, _noop)
    return reg


@pytest.mark.asyncio
async def test_validate_all_passes_when_every_tool_registered():
    conn = _conn_returning([
        ("generate_digest", json.dumps(["calendar_read", "task_db_read"])),
        ("task_decompose", json.dumps([])),
        ("extract_preferences", json.dumps(["task_db_read"])),
    ])
    registry = _registry_with("calendar_read", "task_db_read")
    check = CapabilityToolRegistryCheck(registry=registry, connection=conn)
    await check.validate_all()  # does not raise


@pytest.mark.asyncio
async def test_validate_all_raises_on_unregistered_tool():
    conn = _conn_returning([
        ("prep_research", json.dumps(["task_db_read", "web_search"])),
    ])
    registry = _registry_with("task_db_read")  # web_search missing
    check = CapabilityToolRegistryCheck(registry=registry, connection=conn)
    with pytest.raises(CapabilityToolConfigError) as excinfo:
        await check.validate_all()
    assert "prep_research" in str(excinfo.value)
    assert "web_search" in str(excinfo.value)


@pytest.mark.asyncio
async def test_validate_all_ignores_capabilities_without_tools():
    # Rows with tools_json IS NULL are filtered out by the SQL WHERE clause.
    conn = _conn_returning([])  # simulate SQL filter returning none
    registry = _registry_with()  # empty registry
    check = CapabilityToolRegistryCheck(registry=registry, connection=conn)
    await check.validate_all()  # does not raise


@pytest.mark.asyncio
async def test_validate_all_flags_invalid_json():
    conn = _conn_returning([("broken", "not-json-at-all")])
    registry = _registry_with()
    check = CapabilityToolRegistryCheck(registry=registry, connection=conn)
    with pytest.raises(CapabilityToolConfigError):
        await check.validate_all()


@pytest.mark.asyncio
async def test_validate_all_flags_non_list_tools_json():
    conn = _conn_returning([("wrong_shape", json.dumps({"a": 1}))])
    registry = _registry_with()
    check = CapabilityToolRegistryCheck(registry=registry, connection=conn)
    with pytest.raises(CapabilityToolConfigError):
        await check.validate_all()


@pytest.mark.asyncio
async def test_validate_all_reports_all_mismatches():
    conn = _conn_returning([
        ("cap_a", json.dumps(["missing_a"])),
        ("cap_b", json.dumps(["missing_b", "registered"])),
    ])
    registry = _registry_with("registered")
    check = CapabilityToolRegistryCheck(registry=registry, connection=conn)
    with pytest.raises(CapabilityToolConfigError) as excinfo:
        await check.validate_all()
    message = str(excinfo.value)
    assert "missing_a" in message
    assert "missing_b" in message
