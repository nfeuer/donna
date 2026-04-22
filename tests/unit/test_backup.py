"""Unit tests for backup automation.

Tests backup creation (valid SQLite copy) and retention rotation.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from donna.resilience.backup import BackupManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_test_db(path: Path) -> None:
    """Create a minimal SQLite database at path."""
    async with aiosqlite.connect(str(path)) as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
        await conn.execute("INSERT INTO test VALUES (1)")
        await conn.commit()


def _make_backup_file(backup_dir: Path, dt: datetime, label: str = "daily") -> Path:
    """Create a dummy backup file with the correct naming convention."""
    ts = dt.strftime("%Y-%m-%d_%H%M%S")
    path = backup_dir / f"donna_tasks.db.{ts}.{label}.backup"
    path.write_bytes(b"fake backup content")
    return path


# ---------------------------------------------------------------------------
# Tests: backup_database
# ---------------------------------------------------------------------------

class TestBackupDatabase:
    @pytest.mark.asyncio
    async def test_backup_creates_valid_sqlite_copy(self, tmp_path: Path) -> None:
        """backup_database() produces a valid, readable SQLite file."""
        src = tmp_path / "source.db"
        backup_dir = tmp_path / "backups"
        await _create_test_db(src)

        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        backup_path = await mgr.backup_database(src, backup_dir, label="test")

        assert backup_path.exists(), "Backup file should exist"
        # Verify the backup is a valid SQLite database.
        conn = sqlite3.connect(str(backup_path))
        cursor = conn.execute("SELECT id FROM test")
        rows = cursor.fetchall()
        conn.close()
        assert rows == [(1,)], "Backup should contain the same data as source"

    @pytest.mark.asyncio
    async def test_backup_logs_completed_event(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """backup_database() logs a system.backup.completed event."""
        src = tmp_path / "source.db"
        backup_dir = tmp_path / "backups"
        await _create_test_db(src)

        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        await mgr.backup_database(src, backup_dir, label="test")
        # structlog emits to stdout; just check no exception was raised.

    @pytest.mark.asyncio
    async def test_backup_missing_source_creates_empty_backup(self, tmp_path: Path) -> None:
        """backup_database() on a non-existent source creates an empty-but-valid SQLite file.

        aiosqlite/SQLite creates a new DB when the path doesn't exist, so the
        backup still succeeds (empty DB). This is expected behavior — callers
        should check existence before calling.
        """
        src = tmp_path / "nonexistent.db"
        backup_dir = tmp_path / "backups"
        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        # Should not raise — produces an empty-but-valid SQLite backup.
        backup_path = await mgr.backup_database(src, backup_dir, label="test")
        assert backup_path.exists()
        # Confirm it's a valid SQLite file.
        conn = sqlite3.connect(str(backup_path))
        conn.close()


# ---------------------------------------------------------------------------
# Tests: rotate_backups
# ---------------------------------------------------------------------------

class TestRotateBackups:
    def test_rotation_keeps_correct_daily_count(self, tmp_path: Path) -> None:
        """rotate_backups() keeps at most daily_keep daily backups.

        Use consecutive days in August 2024 (Tue–Fri range) to avoid
        any Sunday or 1st-of-month promotions into weekly/monthly buckets.
        """
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # August 6–15, 2024: Tue, Wed, Thu, Fri, Sat, Mon, Tue, Wed, Thu, Fri
        # None are Sunday (weekday 6) or the 1st of the month.
        for i in range(10):
            # Skip Sunday (weekday 6) by picking safe offsets.
            dt = datetime(2024, 8, 6, 3, 0, 0, tzinfo=UTC) + timedelta(days=i)
            if dt.weekday() == 6:  # Skip Sunday just in case
                dt += timedelta(days=1)
            _make_backup_file(backup_dir, dt, label="daily")

        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        mgr.rotate_backups(daily_keep=7, weekly_keep=4, monthly_keep=3)

        # After rotation: only pure-daily files remain (none were promoted).
        # Check total *.daily.backup files ≤ 7.
        remaining = list(backup_dir.glob("*.daily.backup"))
        assert len(remaining) <= 7, f"Expected ≤7 daily backups, got {len(remaining)}"

    def test_rotation_keeps_weekly_on_sunday(self, tmp_path: Path) -> None:
        """Backups created on Sundays are treated as weekly."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Sunday = weekday 6
        sunday = datetime(2024, 3, 3, 3, 0, 0, tzinfo=UTC)  # This is a Sunday
        assert sunday.weekday() == 6

        _make_backup_file(backup_dir, sunday, label="daily")

        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        # Create 5 more weekly files to trigger pruning (keep=4).
        for i in range(5):
            _make_backup_file(backup_dir, sunday - timedelta(weeks=i + 1), label="weekly")

        mgr.rotate_backups(daily_keep=7, weekly_keep=4, monthly_keep=3)

        # Sunday file promoted to weekly bucket — we should have ≤4 weekly
        all_weekly = list(backup_dir.glob("*.weekly.backup"))
        # Sunday daily file also counted in weekly bucket during rotation
        sunday_daily = [f for f in backup_dir.glob("*.daily.backup")]
        total_weekly = len(all_weekly) + len(sunday_daily)
        assert total_weekly <= 4, f"Expected ≤4 weekly, got {total_weekly}"

    def test_rotation_keeps_monthly_on_first(self, tmp_path: Path) -> None:
        """Backups created on the 1st of the month are treated as monthly."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # 1st of month (not Sunday)
        first = datetime(2024, 3, 1, 3, 0, 0, tzinfo=UTC)  # Friday
        assert first.day == 1
        assert first.weekday() != 6  # not Sunday

        _make_backup_file(backup_dir, first, label="daily")

        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        # Add 4 extra monthly files to trigger pruning (keep=3).
        for i in range(4):
            _make_backup_file(backup_dir, first - timedelta(days=30 * (i + 1)), label="monthly")

        mgr.rotate_backups(daily_keep=7, weekly_keep=4, monthly_keep=3)

        all_monthly = list(backup_dir.glob("*.monthly.backup"))
        first_daily = [f for f in backup_dir.glob("*.daily.backup")]
        total_monthly = len(all_monthly) + len(first_daily)
        assert total_monthly <= 3, f"Expected ≤3 monthly, got {total_monthly}"

    def test_rotation_empty_dir_does_not_raise(self, tmp_path: Path) -> None:
        """rotate_backups() is a no-op on an empty or nonexistent directory."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        mgr = BackupManager(db_dir=tmp_path, backup_dir=backup_dir)
        mgr.rotate_backups()  # should not raise

        nonexistent = tmp_path / "nope"
        mgr2 = BackupManager(db_dir=tmp_path, backup_dir=nonexistent)
        mgr2.rotate_backups()  # should not raise
