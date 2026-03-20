"""SQLite backup automation.

Uses the SQLite .backup() API (not file copy) to safely back up WAL-mode databases.
Runs daily at 3 AM. Retention: 7 daily, 4 weekly (Sunday), 3 monthly (1st).

See docs/resilience.md — Backup Strategy.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog

logger = structlog.get_logger()

_DEFAULT_BACKUP_DIR = Path(os.environ.get("DONNA_BACKUP_DIR", "/donna/backups"))
_DB_NAMES = ["donna_tasks.db", "donna_logs.db"]


class BackupManager:
    """Manages SQLite backups with retention rotation.

    Args:
        db_dir: Directory where the live databases reside.
        backup_dir: Directory where backups are written.
    """

    def __init__(
        self,
        db_dir: Path,
        backup_dir: Path = _DEFAULT_BACKUP_DIR,
    ) -> None:
        self.db_dir = db_dir
        self.backup_dir = backup_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def backup_database(
        self,
        src_path: Path,
        dest_dir: Path,
        label: str,
    ) -> Path:
        """Back up a single SQLite database using the .backup() API.

        Args:
            src_path: Path to the source SQLite file.
            dest_dir: Directory to write the backup into.
            label: Tag embedded in the filename (e.g. "daily", "pre-migration").

        Returns:
            Path of the created backup file.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
        backup_path = dest_dir / f"{src_path.name}.{ts}.{label}.backup"

        log = logger.bind(src=str(src_path), dest=str(backup_path))
        log.info("backup_starting", event_type="system.backup.started")

        try:
            async with aiosqlite.connect(str(src_path)) as src_conn:
                async with aiosqlite.connect(str(backup_path)) as dst_conn:
                    await src_conn.backup(dst_conn)  # type: ignore[attr-defined]

            log.info(
                "backup_completed",
                event_type="system.backup.completed",
                size_bytes=backup_path.stat().st_size,
            )
            return backup_path

        except Exception as exc:
            log.error(
                "backup_failed",
                event_type="system.backup.failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    async def backup_all(self, label: str = "daily") -> list[Path]:
        """Back up all Donna databases.

        Returns:
            List of backup file paths created.
        """
        paths: list[Path] = []
        for db_name in _DB_NAMES:
            src = self.db_dir / db_name
            if not src.exists():
                logger.warning("backup_source_missing", db=db_name)
                continue
            path = await self.backup_database(src, self.backup_dir, label)
            paths.append(path)
        return paths

    async def pre_migration_backup(self) -> list[Path]:
        """Run a labeled backup before Alembic migrations."""
        logger.info("pre_migration_backup_starting", event_type="system.backup.started")
        return await self.backup_all(label="pre-migration")

    def rotate_backups(
        self,
        *,
        daily_keep: int = 7,
        weekly_keep: int = 4,
        monthly_keep: int = 3,
    ) -> None:
        """Prune old backups, keeping the specified retention counts.

        Promotion rules:
        - A daily backup taken on Sunday is promoted to weekly.
        - A daily backup taken on the 1st of the month is promoted to monthly.

        Args:
            daily_keep: Number of most-recent daily backups to retain.
            weekly_keep: Number of weekly backups to retain.
            monthly_keep: Number of monthly backups to retain.
        """
        if not self.backup_dir.exists():
            return

        daily: list[Path] = []
        weekly: list[Path] = []
        monthly: list[Path] = []

        for f in self.backup_dir.glob("*.daily.backup"):
            dt = _parse_backup_datetime(f)
            if dt is None:
                continue
            if dt.weekday() == 6:  # Sunday
                weekly.append(f)
            elif dt.day == 1:
                monthly.append(f)
            else:
                daily.append(f)

        # Also collect files already labeled weekly/monthly
        for f in self.backup_dir.glob("*.weekly.backup"):
            dt = _parse_backup_datetime(f)
            if dt is not None:
                weekly.append(f)

        for f in self.backup_dir.glob("*.monthly.backup"):
            dt = _parse_backup_datetime(f)
            if dt is not None:
                monthly.append(f)

        _prune(daily, daily_keep)
        _prune(weekly, weekly_keep)
        _prune(monthly, monthly_keep)

    async def run_scheduled_backup(self) -> None:
        """Long-running loop: daily backup at 3:00 AM UTC.

        Call via asyncio.create_task(). Runs indefinitely until cancelled.
        """
        while True:
            now = datetime.now(UTC)
            # Seconds until next 03:00 UTC
            next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run = next_run.replace(day=next_run.day + 1)
            delay = (next_run - now).total_seconds()

            logger.info("backup_scheduled", next_run_utc=next_run.isoformat(), delay_s=delay)
            await asyncio.sleep(delay)

            try:
                await self.backup_all(label="daily")
                self.rotate_backups()
            except Exception:
                logger.exception("backup_scheduled_run_failed", event_type="system.backup.failed")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_backup_datetime(path: Path) -> datetime | None:
    """Extract the timestamp from a backup filename.

    Expected format: ``<db_name>.<YYYY-MM-DD_HHMMSS>.<label>.backup``
    """
    try:
        parts = path.name.split(".")
        # parts: [db_name, ts_part, label, "backup"]
        ts_str = parts[-3]  # YYYY-MM-DD_HHMMSS
        return datetime.strptime(ts_str, "%Y-%m-%d_%H%M%S").replace(tzinfo=UTC)
    except (IndexError, ValueError):
        return None


def _prune(files: list[Path], keep: int) -> None:
    """Delete the oldest files, keeping the ``keep`` most recent."""
    sorted_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
    for old in sorted_files[keep:]:
        try:
            old.unlink()
            logger.info("backup_pruned", path=str(old))
        except OSError as exc:
            logger.warning("backup_prune_failed", path=str(old), error=str(exc))
