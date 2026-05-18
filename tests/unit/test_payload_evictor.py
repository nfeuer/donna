"""Tests for donna.collection.payload_evictor.PayloadEvictor."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from donna.collection.payload_evictor import PayloadEvictor
from donna.collection.payload_writer import PayloadWriter


@pytest.fixture
async def db(tmp_path: Path) -> aiosqlite.Connection:
    """Create an in-memory SQLite DB with the invocation_log table."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(
        """
        CREATE TABLE invocation_log (
            id INTEGER PRIMARY KEY,
            payload_path TEXT
        )
        """
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def writer(tmp_path: Path) -> PayloadWriter:
    """Create a PayloadWriter with a small budget for testing."""
    return PayloadWriter(base_dir=tmp_path / "payloads", max_bytes=1000)


def _create_payload_file(base_dir: Path, date_str: str, filename: str, size: int) -> Path:
    """Helper to create a payload file of a specific size."""
    date_dir = base_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    file_path = date_dir / filename
    # Write data of the desired size
    data = "x" * size
    file_path.write_text(data, encoding="utf-8")
    return file_path


async def _insert_log_row(db: aiosqlite.Connection, payload_path: str) -> None:
    """Insert a row into invocation_log with the given payload_path."""
    await db.execute(
        "INSERT INTO invocation_log (payload_path) VALUES (?)",
        (payload_path,),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_no_op_when_under_budget(writer: PayloadWriter, db: aiosqlite.Connection) -> None:
    """Returns empty list when current_bytes <= max_bytes."""
    writer.current_bytes = 500  # well under 1000

    evictor = PayloadEvictor(writer, db)
    result = await evictor.evict()

    assert result == []


@pytest.mark.asyncio
async def test_no_op_when_at_exact_budget(writer: PayloadWriter, db: aiosqlite.Connection) -> None:
    """Returns empty list when current_bytes == max_bytes (not over)."""
    writer.current_bytes = writer.max_bytes

    evictor = PayloadEvictor(writer, db)
    result = await evictor.evict()

    assert result == []


@pytest.mark.asyncio
async def test_evicts_oldest_first(writer: PayloadWriter, db: aiosqlite.Connection) -> None:
    """Oldest date directories are deleted first when over budget."""
    base = writer.base_dir

    # Create 3 date directories with 400 bytes each (total 1200, over 1000 max)
    _create_payload_file(base, "2026-05-01", "inv_a.json", 400)
    _create_payload_file(base, "2026-05-02", "inv_b.json", 400)
    _create_payload_file(base, "2026-05-03", "inv_c.json", 400)

    # Insert corresponding DB rows
    await _insert_log_row(db, "2026-05-01/inv_a.json")
    await _insert_log_row(db, "2026-05-02/inv_b.json")
    await _insert_log_row(db, "2026-05-03/inv_c.json")

    writer.current_bytes = 1200  # over max_bytes=1000

    # target_pct=0.9 means target is 900 bytes
    # Need to free at least 300 bytes → delete oldest (400 bytes gets us to 800)
    evictor = PayloadEvictor(writer, db, target_pct=0.9)
    result = await evictor.evict()

    assert result == ["2026-05-01"]
    assert writer.current_bytes == 800

    # Oldest dir is gone, newer ones remain
    assert not (base / "2026-05-01").exists()
    assert (base / "2026-05-02").exists()
    assert (base / "2026-05-03").exists()


@pytest.mark.asyncio
async def test_evicts_multiple_dirs_if_needed(
    writer: PayloadWriter, db: aiosqlite.Connection,
) -> None:
    """Multiple directories are evicted to reach target."""
    base = writer.base_dir

    # Create 4 date directories with 300 bytes each (total 1200, over 1000 max)
    _create_payload_file(base, "2026-04-01", "inv_1.json", 300)
    _create_payload_file(base, "2026-04-02", "inv_2.json", 300)
    _create_payload_file(base, "2026-04-03", "inv_3.json", 300)
    _create_payload_file(base, "2026-04-04", "inv_4.json", 300)

    await _insert_log_row(db, "2026-04-01/inv_1.json")
    await _insert_log_row(db, "2026-04-02/inv_2.json")
    await _insert_log_row(db, "2026-04-03/inv_3.json")
    await _insert_log_row(db, "2026-04-04/inv_4.json")

    writer.current_bytes = 1200  # over max_bytes=1000

    # target = 0.5 * 1000 = 500, so must free 700 bytes → need 3 dirs (3*300=900 freed)
    evictor = PayloadEvictor(writer, db, target_pct=0.5)
    result = await evictor.evict()

    assert result == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert writer.current_bytes == 300

    assert not (base / "2026-04-01").exists()
    assert not (base / "2026-04-02").exists()
    assert not (base / "2026-04-03").exists()
    assert (base / "2026-04-04").exists()


@pytest.mark.asyncio
async def test_db_updated_for_evicted_dates(
    writer: PayloadWriter, db: aiosqlite.Connection,
) -> None:
    """payload_path is set to NULL in invocation_log for evicted dates."""
    base = writer.base_dir

    _create_payload_file(base, "2026-05-01", "inv_a.json", 400)
    _create_payload_file(base, "2026-05-01", "inv_b.json", 200)
    _create_payload_file(base, "2026-05-02", "inv_c.json", 400)

    await _insert_log_row(db, "2026-05-01/inv_a.json")
    await _insert_log_row(db, "2026-05-01/inv_b.json")
    await _insert_log_row(db, "2026-05-02/inv_c.json")

    writer.current_bytes = 1200  # over 1000

    # target = 0.9 * 1000 = 900 → need to free 300+
    # 2026-05-01 has 600 bytes, freeing it gets us to 600 < 900
    evictor = PayloadEvictor(writer, db, target_pct=0.9)
    result = await evictor.evict()

    assert "2026-05-01" in result

    # Check DB: rows for 2026-05-01 should have NULL payload_path
    cursor = await db.execute(
        "SELECT payload_path FROM invocation_log WHERE payload_path IS NULL"
    )
    null_rows = await cursor.fetchall()
    assert len(null_rows) == 2

    # 2026-05-02 row should still have its path
    cursor = await db.execute(
        "SELECT payload_path FROM invocation_log WHERE payload_path IS NOT NULL"
    )
    remaining_rows = await cursor.fetchall()
    assert len(remaining_rows) == 1
    assert remaining_rows[0][0] == "2026-05-02/inv_c.json"


@pytest.mark.asyncio
async def test_never_raises_on_error(tmp_path: Path, db: aiosqlite.Connection) -> None:
    """Evictor catches exceptions and returns empty list."""
    # Writer pointing to a non-existent base_dir with an impossible state
    writer = PayloadWriter(base_dir=tmp_path / "nonexistent", max_bytes=100)
    writer.current_bytes = 200  # over budget but no dirs to evict

    evictor = PayloadEvictor(writer, db)
    result = await evictor.evict()

    # Should not raise, returns empty list
    assert result == []


@pytest.mark.asyncio
async def test_current_bytes_decremented(writer: PayloadWriter, db: aiosqlite.Connection) -> None:
    """writer.current_bytes is reduced by the size of evicted files."""
    base = writer.base_dir
    _create_payload_file(base, "2026-03-15", "inv_x.json", 500)
    await _insert_log_row(db, "2026-03-15/inv_x.json")

    writer.current_bytes = 1100  # over 1000

    evictor = PayloadEvictor(writer, db, target_pct=0.9)
    await evictor.evict()

    # 1100 - 500 = 600, which is under target of 900
    assert writer.current_bytes == 600


@pytest.mark.asyncio
async def test_ignores_non_date_directories(
    writer: PayloadWriter, db: aiosqlite.Connection,
) -> None:
    """Non-date-formatted directories are not evicted."""
    base = writer.base_dir
    base.mkdir(parents=True, exist_ok=True)

    # Create a non-date directory
    junk_dir = base / "not-a-date"
    junk_dir.mkdir()
    (junk_dir / "file.json").write_text("x" * 500)

    # Create a valid date directory
    _create_payload_file(base, "2026-06-01", "inv.json", 400)
    await _insert_log_row(db, "2026-06-01/inv.json")

    writer.current_bytes = 1100

    evictor = PayloadEvictor(writer, db, target_pct=0.9)
    result = await evictor.evict()

    # Only the date dir should be evicted
    assert result == ["2026-06-01"]
    # Non-date directory should remain
    assert junk_dir.exists()
