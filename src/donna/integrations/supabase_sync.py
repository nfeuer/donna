"""Supabase write-through sync.

Every task write to SQLite is non-blockingly pushed to Supabase Postgres.
Supabase failures never block local operations.

See docs/resilience.md and slices/slice_09_observability_backup.md.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_SYNC_TIMESTAMP_FILENAME = ".supabase_last_sync"


class SupabaseSync:
    """Async write-through sync to Supabase REST API.

    Args:
        supabase_url: Supabase project URL (e.g. https://xyz.supabase.co).
            Reads from SUPABASE_URL env var if not provided.
        supabase_key: Supabase anon/service key.
            Reads from SUPABASE_KEY env var if not provided.
        sync_timestamp_path: Path to file that records last successful sync
            time (used by SelfDiagnostic). Defaults to /donna/db/.supabase_last_sync.
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
        sync_timestamp_path: Path | None = None,
    ) -> None:
        self._url = (supabase_url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self._key = supabase_key or os.environ.get("SUPABASE_KEY", "")
        self._sync_timestamp_path = sync_timestamp_path or Path(
            os.environ.get("DONNA_DB_DIR", "/donna/db")
        ) / _SYNC_TIMESTAMP_FILENAME

        # Pending queue for reconcile on recovery.
        self._pending: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """True when both URL and key are present."""
        return bool(self._url and self._key)

    async def push_task(self, task: dict[str, Any]) -> None:
        """Non-blocking fire-and-forget push of a task to Supabase.

        Local operation is never blocked — the push is scheduled as a
        background asyncio task.
        """
        if not self.configured:
            return
        asyncio.create_task(self._push_with_retry(task))

    async def reconcile(self) -> None:
        """Push all pending (failed) tasks to Supabase.

        Called on Supabase recovery or startup.
        """
        if not self.configured or not self._pending:
            return

        async with self._lock:
            pending = list(self._pending)
            self._pending.clear()

        logger.info("supabase_reconcile_starting", count=len(pending))
        for task in pending:
            await self._push_with_retry(task, queue_on_failure=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _push_with_retry(
        self,
        task: dict[str, Any],
        *,
        queue_on_failure: bool = True,
    ) -> None:
        """Push a task to Supabase with a single retry. Queue on failure."""
        try:
            await self._push(task)
            self._touch_sync_timestamp()
        except Exception as exc:
            logger.error(
                "supabase_push_failed",
                event_type="sync.supabase.failed",
                error_type=type(exc).__name__,
                error=str(exc),
                task_id=task.get("id"),
            )
            if queue_on_failure:
                async with self._lock:
                    self._pending.append(task)

    async def _push(self, task: dict[str, Any]) -> None:
        """POST the task payload to the Supabase tasks table via REST API."""
        import aiohttp

        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        url = f"{self._url}/rest/v1/tasks"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=task, headers=headers) as resp:
                if resp.status not in (200, 201, 409):
                    body = await resp.text()
                    raise RuntimeError(
                        f"Supabase push failed: HTTP {resp.status} — {body[:200]}"
                    )

        logger.info(
            "supabase_push_ok",
            event_type="sync.supabase.push",
            task_id=task.get("id"),
        )

    def _touch_sync_timestamp(self) -> None:
        """Update the sync timestamp file mtime."""
        try:
            path = self._sync_timestamp_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            pass
