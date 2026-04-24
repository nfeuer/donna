"""CorrectionSource — index correction events into the memory store.

Attached via the module-level observer registry (see
:mod:`donna.memory.observers`) rather than widening
``correction_logger.log_correction``'s signature. One chunk per
correction event; body rendered by
:func:`donna.memory.chunking.render_correction_event`.

``source_id`` is the correction row ``id`` so a second log of the
same row upserts the same document. Backfill walks
``correction_log`` and feeds each row through the same template.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.config import CorrectionSourceConfig
from donna.memory.chunking import render_correction_event
from donna.memory.store import Document, MemoryStore

logger = structlog.get_logger()

SOURCE_TYPE = "correction"


class CorrectionSource:
    """Observer + backfill for correction-log rows."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        cfg: CorrectionSourceConfig,
    ) -> None:
        self._store = store
        self._cfg = cfg

    # -- observer -----------------------------------------------------

    async def observe(self, event: dict[str, Any]) -> None:
        """Handle a ``correction_logged`` event (post-commit)."""
        if not self._cfg.enabled:
            return
        try:
            await self._upsert_event(event)
            logger.info(
                "memory_ingest_correction",
                correction_id=event.get("id"),
                user_id=event.get("user_id"),
                field=event.get("field_corrected") or event.get("field"),
            )
        except Exception as exc:
            logger.warning(
                "memory_ingest_failed",
                source_type=SOURCE_TYPE,
                reason=str(exc),
                correction_id=event.get("id"),
            )

    # -- backfill -----------------------------------------------------

    async def backfill(self, user_id: str) -> int:
        if not self._cfg.enabled:
            return 0
        conn = self._store._conn  # type: ignore[attr-defined]
        async with conn.execute(
            "SELECT id, user_id, task_type, task_id, input_text, "
            "       field_corrected, original_value, corrected_value "
            "FROM correction_log WHERE user_id=? ORDER BY timestamp",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        n = 0
        for row in rows:
            event = {
                "id": row[0],
                "user_id": row[1],
                "task_type": row[2],
                "task_id": row[3],
                "input_text": row[4] or "",
                "field_corrected": row[5] or "",
                "original_value": row[6] or "",
                "corrected_value": row[7] or "",
            }
            try:
                await self._upsert_event(event)
                n += 1
            except Exception as exc:
                logger.warning(
                    "memory_ingest_failed",
                    source_type=SOURCE_TYPE,
                    reason=str(exc),
                    correction_id=event["id"],
                )
        logger.info("memory_backfill_correction_done", count=n, user_id=user_id)
        return n

    # -- internals ----------------------------------------------------

    async def _upsert_event(self, event: dict[str, Any]) -> str:
        user_id = str(event.get("user_id") or "nick")
        source_id = str(
            event.get("id")
            or f"{event.get('task_id') or 'na'}:{event.get('field_corrected') or ''}"
        )
        body = render_correction_event(event)
        field = event.get("field_corrected") or event.get("field") or ""
        doc = Document(
            user_id=user_id,
            source_type=SOURCE_TYPE,
            source_id=source_id,
            title=f"Correction: {field}",
            uri=f"correction:{source_id}",
            content=body,
            metadata={
                "task_type": event.get("task_type"),
                "task_id": event.get("task_id"),
                "field": field,
            },
        )
        return await self._store.upsert(doc)


__all__ = ["SOURCE_TYPE", "CorrectionSource"]
