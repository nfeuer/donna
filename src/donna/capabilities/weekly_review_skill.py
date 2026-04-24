"""Slice 16 — weekly self-review scaffold.

Fires Sunday evening (UTC, configurable) via :class:`AsyncCronScheduler`.
Writes ``WeeklyReview/{iso_year}-W{iso_week:02d}.md`` summarising the
week's meetings, completed tasks, logged commitments, and chat
highlights.

Idempotency: the ISO week string (e.g. ``"2026-W17"``). Re-runs within
the same week short-circuit inside :class:`MemoryInformedWriter`.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import aiosqlite
import structlog

from donna.capabilities.daily_reflection_skill import _list_documents
from donna.config import WeeklyReviewSkillConfig
from donna.integrations.vault import VaultClient, VaultReadError
from donna.memory.store import MemoryStore
from donna.memory.writer import MemoryInformedWriter, WriteResult

logger = structlog.get_logger()


def _iso_week_range(week_start: date) -> tuple[date, str]:
    """Return (week_end_exclusive, iso_week_label) for ``week_start``.

    ``week_start`` is expected to be a Monday. The ``iso_week_label``
    follows ``YYYY-Www`` per ISO 8601 (e.g. ``2026-W17``).
    """
    iso_year, iso_week, _ = week_start.isocalendar()
    return week_start + timedelta(days=7), f"{iso_year}-W{iso_week:02d}"


def _week_start_for(day: date) -> date:
    """Normalise ``day`` to the Monday of its ISO week."""
    return day - timedelta(days=day.weekday())


class WeeklyReviewSkill:
    """Compose a week's worth of memory context and delegate to the writer."""

    TEMPLATE = "weekly_review.md.j2"
    TASK_TYPE = "draft_weekly_review"

    def __init__(
        self,
        *,
        writer: MemoryInformedWriter,
        memory_store: MemoryStore,
        vault_client: VaultClient,
        connection: aiosqlite.Connection,
        config: WeeklyReviewSkillConfig,
        user_id: str,
    ) -> None:
        self._writer = writer
        self._memory_store = memory_store
        self._vault_client = vault_client
        self._conn = connection
        self._config = config
        self._user_id = user_id

    async def run_for_week(self, week_start: date) -> WriteResult:
        week_start = _week_start_for(week_start)
        _, iso_week = _iso_week_range(week_start)
        target_path = f"WeeklyReview/{iso_week}.md"

        async def context_gather() -> dict[str, Any]:
            return await self._gather_context(week_start)

        logger.info(
            "weekly_review_triggered",
            iso_week=iso_week,
            user_id=self._user_id,
        )
        return await self._writer.run(
            template=self.TEMPLATE,
            task_type=self.TASK_TYPE,
            context_gather=context_gather,
            target_path=target_path,
            idempotency_key=iso_week,
            user_id=self._user_id,
            autonomy_level=self._config.autonomy_level,
        )

    async def _gather_context(self, week_start: date) -> dict[str, Any]:
        limits = self._config.context_limits
        week_end, iso_week = _iso_week_range(week_start)
        start = datetime.combine(week_start, time.min)
        end = datetime.combine(week_end, time.min)

        meetings = await _list_documents(
            self._conn,
            user_id=self._user_id,
            source_type="vault",
            updated_from=start,
            updated_to=end,
            path_prefix="Meetings/",
            limit=limits.meetings,
        )
        completed_tasks = await _list_documents(
            self._conn,
            user_id=self._user_id,
            source_type="task",
            updated_from=start,
            updated_to=end,
            metadata_status_in={"done", "cancelled"},
            limit=limits.completed_tasks,
        )
        commitments = await _list_documents(
            self._conn,
            user_id=self._user_id,
            source_type="vault",
            updated_from=start,
            updated_to=end,
            path_prefix="Commitments/",
            limit=limits.commitments,
        )
        chat_highlights = await _list_documents(
            self._conn,
            user_id=self._user_id,
            source_type="chat",
            updated_from=start,
            updated_to=end,
            limit=limits.chat_highlights,
        )

        prior_review_preview = await self._load_prior_review(week_start)

        return {
            "week": {
                "iso": iso_week,
                "start": week_start.isoformat(),
                "end": (week_end - timedelta(days=1)).isoformat(),
            },
            "meetings": meetings,
            "completed_tasks": completed_tasks,
            "commitments": commitments,
            "chat_highlights": chat_highlights,
            "prior_review": prior_review_preview,
        }

    async def _load_prior_review(
        self, week_start: date
    ) -> dict[str, Any] | None:
        prev_start = week_start - timedelta(days=7)
        _, prev_iso = _iso_week_range(prev_start)
        path = f"WeeklyReview/{prev_iso}.md"
        try:
            note = await self._vault_client.read(path)
        except VaultReadError:
            return None
        return {
            "iso": prev_iso,
            "path": path,
            "frontmatter": dict(note.frontmatter),
            "preview": note.content[:400],
        }
