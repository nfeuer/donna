"""Tests for donna.collection.payload_writer.PayloadWriter."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from donna.collection.payload_writer import PayloadWriter


@pytest.fixture
def writer(tmp_path: Path) -> PayloadWriter:
    """Create a PayloadWriter pointed at a temp directory."""
    return PayloadWriter(base_dir=tmp_path / "payloads", max_bytes=10_000)


@pytest.mark.asyncio
async def test_successful_write_creates_valid_json(writer: PayloadWriter) -> None:
    """Write creates a JSON file with request and response keys."""
    request = {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "hi"}]}
    response = {"id": "msg_123", "content": [{"text": "hello"}]}

    result = await writer.write("inv_001", request, response, for_date=date(2026, 5, 16))

    assert result == "2026-05-16/inv_001.json"
    file_path = writer.base_dir / result
    assert file_path.exists()

    data = json.loads(file_path.read_text(encoding="utf-8"))
    assert data["request"] == request
    assert data["response"] == response


@pytest.mark.asyncio
async def test_write_uses_today_when_no_date(writer: PayloadWriter) -> None:
    """When for_date is omitted, uses today's date for the partition."""
    result = await writer.write("inv_002", {"a": 1}, {"b": 2})

    assert result is not None
    today_str = date.today().isoformat()
    assert result.startswith(today_str)


@pytest.mark.asyncio
async def test_write_failure_returns_none(tmp_path: Path) -> None:
    """Permission denied or OSError returns None without raising."""
    # Point writer at a file (not a directory) so mkdir fails
    blocker = tmp_path / "payloads"
    blocker.write_text("not a directory")
    writer = PayloadWriter(base_dir=blocker, max_bytes=10_000)

    result = await writer.write("inv_003", {"x": 1}, {"y": 2}, for_date=date(2026, 1, 1))

    assert result is None


@pytest.mark.asyncio
async def test_current_bytes_increments_on_write(writer: PayloadWriter) -> None:
    """current_bytes tracks cumulative bytes written."""
    assert writer.current_bytes == 0

    await writer.write("inv_a", {"req": "a"}, {"res": "a"}, for_date=date(2026, 5, 1))
    first_size = writer.current_bytes
    assert first_size > 0

    await writer.write("inv_b", {"req": "b"}, {"res": "b"}, for_date=date(2026, 5, 1))
    assert writer.current_bytes > first_size


@pytest.mark.asyncio
async def test_current_bytes_not_incremented_on_failure(tmp_path: Path) -> None:
    """On write failure, current_bytes remains unchanged."""
    blocker = tmp_path / "payloads"
    blocker.write_text("not a directory")
    writer = PayloadWriter(base_dir=blocker, max_bytes=10_000)

    await writer.write("inv_fail", {"x": 1}, {"y": 2})
    assert writer.current_bytes == 0


@pytest.mark.asyncio
async def test_budget_exceeded_returns_none(writer: PayloadWriter) -> None:
    """When current_bytes >= max_bytes, write returns None."""
    writer.current_bytes = writer.max_bytes  # simulate full

    result = await writer.write("inv_over", {"a": 1}, {"b": 2})
    assert result is None


@pytest.mark.asyncio
async def test_sync_size_from_disk(writer: PayloadWriter) -> None:
    """sync_size_from_disk recalculates actual total from disk."""
    # Write some payloads
    await writer.write("inv_x", {"data": "x" * 100}, {"out": "y"}, for_date=date(2026, 3, 1))
    await writer.write("inv_y", {"data": "z" * 200}, {"out": "w"}, for_date=date(2026, 3, 2))

    expected = writer.current_bytes

    # Reset counter and resync
    writer.current_bytes = 0
    synced = await writer.sync_size_from_disk()

    assert synced == expected
    assert writer.current_bytes == expected


@pytest.mark.asyncio
async def test_sync_size_from_disk_empty(tmp_path: Path) -> None:
    """sync_size_from_disk returns 0 when no files exist."""
    writer = PayloadWriter(base_dir=tmp_path / "empty_payloads")
    result = await writer.sync_size_from_disk()
    assert result == 0
    assert writer.current_bytes == 0


@pytest.mark.asyncio
async def test_multiple_dates_partitioned(writer: PayloadWriter) -> None:
    """Payloads for different dates go into separate directories."""
    await writer.write("inv_d1", {}, {}, for_date=date(2026, 1, 1))
    await writer.write("inv_d2", {}, {}, for_date=date(2026, 1, 2))

    assert (writer.base_dir / "2026-01-01" / "inv_d1.json").exists()
    assert (writer.base_dir / "2026-01-02" / "inv_d2.json").exists()
