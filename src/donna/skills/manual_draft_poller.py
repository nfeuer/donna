"""Polls skill_candidate_report for manual_draft_at triggers and runs AutoDrafter.

F-W1-D from Wave 2 plan. The API process sets manual_draft_at to now on
POST /admin/skill-candidates/{id}/draft-now; the orchestrator (this
poller) picks up and drives AutoDrafter.draft_one, clearing the column
on completion (or failure — always clear to prevent infinite retry).

Mirrors the automation.next_run_at pattern from Wave 1 F-6.
"""

from __future__ import annotations

from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


class ManualDraftPoller:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        auto_drafter: Any,
        candidate_repo: Any,
        batch_size: int = 5,
    ) -> None:
        self._conn = connection
        self._auto_drafter = auto_drafter
        self._repo = candidate_repo
        self._batch_size = batch_size

    async def run_once(self) -> int:
        """Pick up pending manual-draft triggers, run AutoDrafter, clear the column.

        Returns the number of candidates processed (successfully or not).
        """
        cursor = await self._conn.execute(
            "SELECT id FROM skill_candidate_report "
            "WHERE manual_draft_at IS NOT NULL AND status = 'new' "
            "ORDER BY manual_draft_at ASC LIMIT ?",
            (self._batch_size,),
        )
        rows = await cursor.fetchall()
        picked = 0
        for (candidate_id,) in rows:
            candidate = await self._repo.get(candidate_id)
            if candidate is None:
                logger.warning(
                    "manual_draft_candidate_not_found", candidate_id=candidate_id,
                )
            else:
                try:
                    await self._auto_drafter.draft_one(candidate)
                except Exception:
                    logger.exception(
                        "manual_draft_failed", candidate_id=candidate_id,
                    )
            # Always clear the column — success or failure — to prevent
            # infinite retry on a permanently broken candidate.
            await self._conn.execute(
                "UPDATE skill_candidate_report SET manual_draft_at = NULL WHERE id = ?",
                (candidate_id,),
            )
            await self._conn.commit()
            picked += 1
        return picked
