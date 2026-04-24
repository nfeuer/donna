"""Slice 16 — daily commitment-log scaffold.

Fires nightly via :class:`AsyncCronScheduler`. Writes
``Commitments/{YYYY-MM-DD}.md`` summarising the day's commitments
(promises made / accepted) extracted from chat + task sources by the
``extract_commitments`` task_type.

Design choice: one file per day (not a running log). Git history over
the ``Commitments/`` folder gives the running view; the per-day file
keeps idempotency trivial (``idempotency_key = day.isoformat()``) and
lets the user edit individual days without merge drama.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import aiosqlite
import structlog

from donna.capabilities.daily_reflection_skill import _list_documents
from donna.config import CommitmentLogSkillConfig
from donna.memory.store import MemoryStore
from donna.memory.writer import MemoryInformedWriter, WriteResult

logger = structlog.get_logger()


class CommitmentLogSkill:
    """Compose today's chat + task signals and delegate to the writer."""

    TEMPLATE = "commitment_log.md.j2"
    TASK_TYPE = "extract_commitments"

    def __init__(
        self,
        *,
        writer: MemoryInformedWriter,
        memory_store: MemoryStore,
        connection: aiosqlite.Connection,
        config: CommitmentLogSkillConfig,
        user_id: str,
    ) -> None:
        self._writer = writer
        self._memory_store = memory_store
        self._conn = connection
        self._config = config
        self._user_id = user_id

    async def run_for_day(self, day: date) -> WriteResult:
        target_path = f"Commitments/{day:%Y-%m-%d}.md"

        async def context_gather() -> dict[str, Any]:
            return await self._gather_context(day)

        logger.info(
            "commitment_log_triggered",
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

        chat_docs = await _list_documents(
            self._conn,
            user_id=self._user_id,
            source_type="chat",
            updated_from=start,
            updated_to=end,
            limit=limits.chat_hits,
        )
        task_docs = await _list_documents(
            self._conn,
            user_id=self._user_id,
            source_type="task",
            updated_from=start,
            updated_to=end,
            limit=limits.task_hits,
        )

        return {
            "day": {
                "iso": day.isoformat(),
                "label": day.strftime("%A, %B %-d %Y"),
            },
            "chat_signals": chat_docs,
            "task_signals": task_docs,
        }
