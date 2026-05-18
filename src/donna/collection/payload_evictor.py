"""Evicts oldest payload directories when disk budget is exceeded.

Pairs with PayloadWriter to enforce storage limits by removing the oldest
date-partitioned directories first and nullifying the corresponding
``payload_path`` entries in the invocation_log table.

Part of the Claude Inspector feature (§9 forensics tooling).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import aiosqlite
import structlog

from donna.collection.payload_writer import PayloadWriter

logger = structlog.get_logger()


class PayloadEvictor:
    """Removes oldest payload directories to reclaim disk space.

    When ``current_bytes`` on the writer exceeds ``max_bytes``, the evictor
    deletes date directories (oldest first) until usage drops below
    ``target_pct * max_bytes``.

    For each evicted date directory, the corresponding ``payload_path`` entries
    in ``invocation_log`` are set to NULL.

    Args:
        writer: The PayloadWriter whose storage is being managed.
        db: An open aiosqlite connection to the task database.
        target_pct: Fraction of max_bytes to target after eviction (default 0.9).
    """

    def __init__(
        self,
        writer: PayloadWriter,
        db: aiosqlite.Connection,
        target_pct: float = 0.9,
    ) -> None:
        self._writer = writer
        self._db = db
        self._target_pct = target_pct

    async def evict(self) -> list[str]:
        """Run eviction if the writer is over budget.

        Returns:
            List of evicted date strings (e.g. ``["2026-05-01", "2026-05-02"]``).
            Empty list if no eviction was needed or on error.
        """
        try:
            return await self._evict_impl()
        except Exception as exc:
            logger.warning(
                "payload_evictor.evict_failed",
                error=str(exc),
                exc_info=True,
            )
            return []

    async def _evict_impl(self) -> list[str]:
        """Internal eviction logic."""
        if self._writer.current_bytes <= self._writer.max_bytes:
            return []

        target_bytes = int(self._target_pct * self._writer.max_bytes)
        base_dir = self._writer.base_dir

        # List date directories sorted oldest first
        date_dirs = self._list_date_dirs(base_dir)
        if not date_dirs:
            logger.warning(
                "payload_evictor.no_dirs_to_evict",
                current_bytes=self._writer.current_bytes,
                max_bytes=self._writer.max_bytes,
            )
            return []

        evicted: list[str] = []

        for date_dir in date_dirs:
            if self._writer.current_bytes <= target_bytes:
                break

            date_str = date_dir.name
            dir_size = self._dir_size(date_dir)

            try:
                shutil.rmtree(date_dir)
            except OSError as exc:
                logger.warning(
                    "payload_evictor.rmtree_failed",
                    date=date_str,
                    error=str(exc),
                )
                continue

            # Update DB — nullify payload_path for this date
            try:
                await self._db.execute(
                    "UPDATE invocation_log SET payload_path = NULL WHERE payload_path LIKE ?",
                    (f"{date_str}/%",),
                )
            except Exception as exc:
                logger.warning(
                    "payload_evictor.db_update_failed",
                    date=date_str,
                    error=str(exc),
                )
                # Directory is already gone; continue with bookkeeping
                # but don't add to evicted list since DB wasn't updated
                self._writer.current_bytes -= dir_size
                continue

            self._writer.current_bytes -= dir_size
            evicted.append(date_str)

            logger.info(
                "payload_evictor.evicted",
                date=date_str,
                freed_bytes=dir_size,
                current_bytes=self._writer.current_bytes,
            )

        # Commit all DB changes at once
        if evicted:
            try:
                await self._db.commit()
            except Exception as exc:
                logger.warning(
                    "payload_evictor.commit_failed",
                    error=str(exc),
                )

        return evicted

    @staticmethod
    def _list_date_dirs(base_dir: Path) -> list[Path]:
        """Return date directories sorted oldest first.

        Only includes directories whose names look like ISO dates (YYYY-MM-DD).
        """
        if not base_dir.exists():
            return []

        dirs: list[Path] = []
        try:
            for entry in base_dir.iterdir():
                if entry.is_dir() and len(entry.name) == 10 and entry.name[4] == "-":
                    dirs.append(entry)
        except OSError:
            return []

        dirs.sort(key=lambda d: d.name)
        return dirs

    @staticmethod
    def _dir_size(path: Path) -> int:
        """Calculate total size of all files in a directory."""
        total = 0
        try:
            for file in path.rglob("*"):
                if file.is_file():
                    try:
                        total += file.stat().st_size
                    except OSError:
                        continue
        except OSError:
            pass
        return total
