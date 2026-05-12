"""Thread conversation memory for the Universal Reply Handler.

Stores a rolling window of messages per thread in SQLite. Used to
provide conversation context to the LLM when classifying complex replies.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
import uuid6

logger = structlog.get_logger()


class ThreadMemory:
    """Read/write conversation memory for a thread.

    Args:
        conn: aiosqlite connection with thread_memory table.
        window_size: Max messages to include in LLM context.
    """

    def __init__(self, conn: Any, window_size: int = 10) -> None:
        self._conn = conn
        self._window_size = window_size

    async def record(
        self,
        thread_id: str,
        context_type: str,
        task_id: str | None,
        role: str,
        content: str,
    ) -> None:
        """Append a message to thread memory."""
        now = datetime.now(tz=UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO thread_memory (id, thread_id, context_type, task_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid6.uuid7()), thread_id, context_type, task_id, role, content, now),
        )
        await self._conn.commit()

    async def retrieve(self, thread_id: str) -> list[dict[str, Any]]:
        """Return the last N messages for a thread, ordered oldest-first."""
        cursor = await self._conn.execute(
            "SELECT role, content, created_at FROM thread_memory "
            "WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
            (thread_id, self._window_size),
        )
        rows = await cursor.fetchall()
        rows.reverse()
        return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in rows]

    async def prune(self, retention_days: int = 7) -> int:
        """Delete messages older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(tz=UTC) - timedelta(days=retention_days)).isoformat()
        cursor = await self._conn.execute(
            "DELETE FROM thread_memory WHERE created_at < ?",
            (cutoff,),
        )
        await self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.info("thread_memory_pruned", deleted=count)
        return count
