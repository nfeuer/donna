"""TaskSource — index task mutations into the memory store.

Observes :meth:`donna.tasks.database.Database.create_task` and
``update_task``. The re-embed policy matches the slice brief:

- re-embed when the semantic fields (``title`` / ``description`` /
  ``notes_json``) changed since the last upsert, OR
- force a re-embed on a status transition into a configured terminal
  state (default ``done`` / ``cancelled``) so the final-state context
  lands in the index.

``source_id`` is the task UUID. Failures are swallowed + logged
(``memory_ingest_failed``) so the DB write never unwinds.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

from donna.config import TaskSourceConfig
from donna.memory.chunking import TaskChunker
from donna.memory.store import Document, MemoryStore

logger = structlog.get_logger()

SOURCE_TYPE = "task"


def _task_content_hash(task: dict[str, Any]) -> str:
    """Hash the fields that trigger re-embedding when they change."""
    payload = "||".join(
        [
            str(task.get("title") or ""),
            str(task.get("description") or ""),
            str(task.get("notes") or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TaskSource:
    """Observer + backfill for task mutations."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        cfg: TaskSourceConfig,
    ) -> None:
        self._store = store
        self._cfg = cfg
        self._chunker = TaskChunker(max_tokens=256)
        self._reindex_statuses = {s.lower() for s in cfg.reindex_on_status}

    # -- observer -----------------------------------------------------

    async def observe_task(self, event: dict[str, Any]) -> None:
        """Handle a ``task_created`` or ``task_updated`` event.

        Event shape: ``{"action": "create" | "update" | "delete",
        "task": {...}, "previous_status": str | None}``.
        """
        if not self._cfg.enabled:
            return
        action = str(event.get("action") or "update")
        task = event.get("task") or {}
        if not task:
            return
        user_id = str(task.get("user_id") or "nick")
        task_id = str(task.get("id") or "")
        if not task_id:
            return
        if action == "delete":
            try:
                await self._store.delete(
                    source_type=SOURCE_TYPE,
                    source_id=task_id,
                    user_id=user_id,
                )
            except Exception as exc:
                logger.warning(
                    "memory_ingest_failed",
                    source_type=SOURCE_TYPE,
                    reason=str(exc),
                    task_id=task_id,
                )
            return
        try:
            await self._upsert_task(task, previous_status=event.get("previous_status"))
            logger.info(
                "memory_ingest_task",
                task_id=task_id,
                status=task.get("status"),
                action=action,
            )
        except Exception as exc:
            logger.warning(
                "memory_ingest_failed",
                source_type=SOURCE_TYPE,
                reason=str(exc),
                task_id=task_id,
            )

    # -- backfill -----------------------------------------------------

    async def backfill(self, user_id: str) -> int:
        """Re-ingest every non-deleted task row for ``user_id``.

        ``tasks`` currently lacks a soft-delete column; we filter on
        every non-tombstoned row. Once a soft-delete lands the filter
        extends to ``deleted_at IS NULL``.
        """
        if not self._cfg.enabled:
            return 0
        conn = self._store._conn  # type: ignore[attr-defined]
        async with conn.execute(
            "SELECT id, user_id, title, description, status, domain, "
            "       priority, deadline, notes "
            "FROM tasks WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        n = 0
        for row in rows:
            task = {
                "id": row[0],
                "user_id": row[1],
                "title": row[2],
                "description": row[3],
                "status": row[4],
                "domain": row[5],
                "priority": row[6],
                "deadline": row[7],
                "notes": row[8],
            }
            try:
                await self._upsert_task(task)
                n += 1
            except Exception as exc:
                logger.warning(
                    "memory_ingest_failed",
                    source_type=SOURCE_TYPE,
                    reason=str(exc),
                    task_id=task["id"],
                )
        logger.info("memory_backfill_task_done", count=n, user_id=user_id)
        return n

    # -- internals ----------------------------------------------------

    async def _upsert_task(
        self, task: dict[str, Any], *, previous_status: str | None = None
    ) -> str:
        user_id = str(task.get("user_id") or "nick")
        task_id = str(task["id"])
        current_status = str(task.get("status") or "").lower()
        prev_status_norm = (previous_status or "").lower() or None
        # Re-embed policy: unconditional when current status is in the
        # configured terminal set AND the previous status differed —
        # final-state context is high-signal. For non-terminal updates
        # we lean on the MemoryStore content-hash short-circuit.
        force_reindex = (
            current_status in self._reindex_statuses
            and prev_status_norm != current_status
        )
        body = self._chunker.render(task)
        doc = Document(
            user_id=user_id,
            source_type=SOURCE_TYPE,
            source_id=task_id,
            title=str(task.get("title") or "(untitled task)"),
            uri=f"task:{task_id}",
            content=body,
            metadata={
                "status": task.get("status"),
                "domain": task.get("domain"),
                "priority": task.get("priority"),
                "deadline": task.get("deadline"),
            },
        )
        if force_reindex:
            # Bust the content-hash short-circuit so the upsert fully
            # re-embeds even if the notes/title text is identical.
            conn = self._store._conn  # type: ignore[attr-defined]
            await conn.execute(
                "UPDATE memory_documents SET content_hash='' "
                "WHERE user_id=? AND source_type=? AND source_id=?",
                (user_id, SOURCE_TYPE, task_id),
            )
            await conn.commit()
        return await self._store.upsert(doc)


__all__ = ["SOURCE_TYPE", "TaskSource"]
