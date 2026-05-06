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


def _conn_returning(
    rows: list[tuple],
    *,
    default_status: str = "active",
    default_trigger_type: str = "on_schedule",
) -> MagicMock:
    """Build a mock connection.

    Each row in ``rows`` may be a 2-tuple ``(name, tools_json)`` (slice
    21 schema, before slice 22 added status/trigger_type to the SELECT)
    or a 4-tuple ``(name, tools_json, status, trigger_type)``. The
    helper normalises 2-tuples to 4-tuples using the defaults above so
    pre-slice-22 tests keep treating the row as fatal-eligible.
    """
    normalised: list[tuple] = []
    for row in rows:
        if len(row) == 2:
            normalised.append((row[0], row[1], default_status, default_trigger_type))
        else:
            normalised.append(tuple(row))
    conn = MagicMock()
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=normalised)
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


# ---------------------------------------------------------------------------
# Slice 22 — speculative emission for non-fatal mismatches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_all_emits_speculative_for_pending_review():
    """A mismatch on pending_review capability surfaces but doesn't raise."""
    rows = [
        ("dormant_cap", json.dumps(["missing_tool"]), "pending_review", "on_schedule"),
    ]
    conn = _conn_returning(rows)
    registry = _registry_with()
    surfaced: list = []

    class _StubSurfacer:
        async def surface(self, gap):
            surfaced.append(gap)

    check = CapabilityToolRegistryCheck(
        registry=registry, connection=conn, surfacer=_StubSurfacer(),
    )
    await check.validate_all()  # does NOT raise
    assert len(surfaced) == 1
    assert surfaced[0].tool_name == "missing_tool"
    assert surfaced[0].severity == "speculative"
    assert surfaced[0].blocking_capability_id == "dormant_cap"


@pytest.mark.asyncio
async def test_validate_all_emits_speculative_for_on_manual_trigger():
    """Active but on_manual capability is speculative-only, no raise."""
    rows = [
        ("manual_cap", json.dumps(["missing_tool"]), "active", "on_manual"),
    ]
    conn = _conn_returning(rows)
    registry = _registry_with()
    surfaced: list = []

    class _StubSurfacer:
        async def surface(self, gap):
            surfaced.append(gap)

    check = CapabilityToolRegistryCheck(
        registry=registry, connection=conn, surfacer=_StubSurfacer(),
    )
    await check.validate_all()  # does NOT raise
    assert len(surfaced) == 1
    assert surfaced[0].severity == "speculative"


@pytest.mark.asyncio
async def test_validate_all_still_raises_on_active_scheduled_with_speculative_surfaced():
    """Mixed batch: speculative gets surfaced, fatal still raises."""
    rows = [
        ("dormant_cap", json.dumps(["missing_a"]), "pending_review", "on_schedule"),
        ("live_cap", json.dumps(["missing_b"]), "active", "on_schedule"),
    ]
    conn = _conn_returning(rows)
    registry = _registry_with()
    surfaced: list = []

    class _StubSurfacer:
        async def surface(self, gap):
            surfaced.append(gap)

    check = CapabilityToolRegistryCheck(
        registry=registry, connection=conn, surfacer=_StubSurfacer(),
    )
    with pytest.raises(CapabilityToolConfigError) as excinfo:
        await check.validate_all()
    # Speculative was surfaced even though boot then died.
    assert len(surfaced) == 1
    assert surfaced[0].tool_name == "missing_a"
    # Fatal subset only.
    assert "missing_b" in str(excinfo.value)
    assert "missing_a" not in str(excinfo.value)
