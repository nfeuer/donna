"""Slice 16 — end-of-day reflection scaffold skill.

Fires nightly from an :class:`AsyncCronScheduler` and writes a reflection
scaffold under ``Reflections/{YYYY-MM-DD}.md``. The skill delegates all
write-path concerns (autonomy redirect, idempotency, LLM routing,
rendering, commit) to the shared
:class:`~donna.memory.writer.MemoryInformedWriter`.

Context gathered for the LLM prompt:

- today's meeting notes (source_type=vault, path_prefix=Meetings/,
  metadata.event_start on ``day``)
- today's task mutations (source_type=task, updated_at on ``day``,
  status terminal — i.e. the ``reindex_on_status`` set the TaskSource
  already re-embeds on)
- chat highlights (source_type=chat, updated_at on ``day``, capped)

The skill intentionally never attempts to surface *every* event — it
produces a scaffold nudging the user to reflect, not a transcript.

Idempotency: ``idempotency_key = day.isoformat()``. Re-running within
the same day short-circuits inside :class:`MemoryInformedWriter`.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from typing import Any

import aiosqlite
import structlog

from donna.config import DailyReflectionSkillConfig
from donna.memory.store import MemoryStore, RetrievedChunk
from donna.memory.writer import MemoryInformedWriter, WriteResult

logger = structlog.get_logger()


class DailyReflectionSkill:
    """Compose a day's worth of memory context and delegate to the writer."""

    TEMPLATE = "daily_reflection.md.j2"
    TASK_TYPE = "draft_daily_reflection"

    def __init__(
        self,
        *,
        writer: MemoryInformedWriter,
        memory_store: MemoryStore,
        connection: aiosqlite.Connection,
        config: DailyReflectionSkillConfig,
        user_id: str,
    ) -> None:
        self._writer = writer
        self._memory_store = memory_store
        self._conn = connection
        self._config = config
        self._user_id = user_id

    async def run_for_day(self, day: date) -> WriteResult:
        target_path = f"Reflections/{day:%Y-%m-%d}.md"

        async def context_gather() -> dict[str, Any]:
            return await self._gather_context(day)

        logger.info(
            "daily_reflection_triggered",
            day=day.isoformat(),
            user_id=self._user_id,
        )
        return await self._writer.run(
            template=self.TEMPLATE,
            task_type=self.TASK_TYPE,
            context_gather=context_gather,
            target_path=target_path,
            idempotency_key=day.isoformat(),
            user_id=self._user_id,
            autonomy_level=self._config.autonomy_level,
        )

    async def _gather_context(self, day: date) -> dict[str, Any]:
        limits = self._config.context_limits
        start = datetime.combine(day, time.min)
        end = start + timedelta(days=1)

        meetings_today, completed_tasks = await asyncio.gather(
            _list_documents(
                self._conn,
                user_id=self._user_id,
                source_type="vault",
                updated_from=start,
                updated_to=end,
                path_prefix="Meetings/",
                limit=limits.meetings,
            ),
            _list_documents(
                self._conn,
                user_id=self._user_id,
                source_type="task",
                updated_from=start,
                updated_to=end,
                metadata_status_in={"done", "cancelled"},
                limit=limits.completed_tasks,
            ),
        )

        # Chat highlights: semantic, anchored on the day's keywords. The
        # query is intentionally generic so top-K trends toward the
        # day's most active topics; post-filter to keep to the day.
        chat_hits = await self._memory_store.search(
            query=f"reflection {day.isoformat()}",
            user_id=self._user_id,
            k=limits.chat_highlights * 3,
            sources=["chat"],
        )
        chat_highlights = _filter_within_window(
            chat_hits, start, end
        )[: limits.chat_highlights]

        return {
            "day": {
                "iso": day.isoformat(),
                "label": day.strftime("%A, %B %-d %Y"),
            },
            "meetings": meetings_today,
            "completed_tasks": completed_tasks,
            "chat_highlights": [_hit_to_dict(h) for h in chat_highlights],
        }


# ---------------------------------------------------------------------------
# Shared helpers (also used by commitment_log + weekly_review). Kept private
# to the capabilities package — not promoted into MemoryStore because the
# returned shape is already the Jinja projection, not a stable public record.
# ---------------------------------------------------------------------------


async def _list_documents(
    conn: aiosqlite.Connection,
    *,
    user_id: str,
    source_type: str,
    updated_from: datetime,
    updated_to: datetime,
    path_prefix: str | None = None,
    metadata_status_in: set[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lightweight doc-level read for date-bounded skill context gathering.

    Returns a list of ``{source_id, title, content_preview, metadata}``
    dicts ordered by ``updated_at DESC``. Soft-deleted rows excluded.
    """
    sql = (
        "SELECT source_id, title, metadata_json, updated_at, id "
        "FROM memory_documents "
        "WHERE user_id=? AND source_type=? AND deleted_at IS NULL "
        "  AND updated_at >= ? AND updated_at < ? "
    )
    params: list[Any] = [
        user_id,
        source_type,
        updated_from.isoformat(),
        updated_to.isoformat(),
    ]
    if path_prefix:
        sql += "  AND source_id LIKE ? "
        params.append(f"{path_prefix}%")
    sql += "ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()

    out: list[dict[str, Any]] = []
    for source_id, title, metadata_json, _updated_at, doc_id in rows:
        metadata = (
            json.loads(metadata_json) if metadata_json else {}
        )
        if metadata_status_in is not None:
            status = str(metadata.get("status") or "").lower()
            if status not in metadata_status_in:
                continue
        preview = await _first_chunk_preview(conn, doc_id)
        out.append(
            {
                "source_id": source_id,
                "title": title or "",
                "metadata": metadata,
                "content_preview": preview,
            }
        )
    return out


async def _first_chunk_preview(
    conn: aiosqlite.Connection, document_id: str, cap: int = 240
) -> str:
    async with conn.execute(
        "SELECT content FROM memory_chunks "
        "WHERE document_id=? ORDER BY chunk_index LIMIT 1",
        (document_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row or not row[0]:
        return ""
    text = str(row[0])
    if len(text) > cap:
        text = text[:cap].rstrip() + "…"
    return text


def _filter_within_window(
    hits: list[RetrievedChunk],
    start: datetime,
    end: datetime,
) -> list[RetrievedChunk]:
    """Keep only hits whose metadata ``mtime`` (or equivalent) lies in ``[start, end)``."""
    kept: list[RetrievedChunk] = []
    for hit in hits:
        mtime = hit.metadata.get("mtime") if hit.metadata else None
        if mtime is None:
            continue
        try:
            ts = float(mtime)
        except (TypeError, ValueError):
            continue
        hit_dt = datetime.fromtimestamp(ts)
        if start <= hit_dt < end:
            kept.append(hit)
    return kept


def _hit_to_dict(hit: RetrievedChunk) -> dict[str, Any]:
    return {
        "title": hit.title or "",
        "source_path": hit.source_path,
        "content": hit.content,
        "score": hit.score,
    }
