"""Apply chat-mode escalation submissions back to their originating task.

Realizes the result-ingestion contract from
``docs/superpowers/specs/manual-escalation.md`` §5.2 and §10.10. Mirrors
the polling pattern of :mod:`donna.skills.manual_draft_poller` and
:class:`donna.notifications.escalation_delivery_loop.EscalationDeliveryLoop`:

- One coroutine per process polls every ``tick_seconds`` (default 30s).
- Picks ``escalation_request`` rows where ``mode='chat' AND
  status='submitted' AND task_id IS NOT NULL``.
- Reads the answer out of ``result`` (stored as a JSON envelope per
  ``schemas/escalation_submission.json``).
- Appends ``[escalation:<correlation_id>] <answer>`` to the originating
  task's ``notes`` and transitions the task to ``done``.
- Marks the row ``status='validated'`` with a small JSON
  ``validation_result`` so the dashboard timeline shows the ingestion
  was applied successfully.
- Writes an ``escalation_validated`` audit row.

On a per-row exception the row is left in ``submitted`` so the next tick
re-tries; the iteration cap on the user side bounds the user's number
of attempts, not Donna's number of ingestion retries (we only retry
*system* failures, not user-supplied bad answers).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from donna.cost.escalation_audit import write_escalation_event
from donna.tasks.db_models import TaskStatus
from donna.tasks.state_machine import InvalidTransitionError

if TYPE_CHECKING:
    import aiosqlite

    from donna.tasks.database import Database

logger = structlog.get_logger()


DEFAULT_TICK_SECONDS = 30
"""Polling cadence — half the delivery loop's 60s tick so chat answers
land quickly without overwhelming SQLite."""

EVENT_VALIDATED = "escalation_validated"
"""Audit-log event name written when a chat-mode answer ingests OK."""


class ChatEscalationIngestionPoller:
    """Picks up chat-mode submissions and applies them to tasks."""

    def __init__(
        self,
        *,
        db: Database,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
    ) -> None:
        self._db = db
        self._tick_seconds = tick_seconds

    async def run(self) -> None:
        """Background entrypoint — schedule from server.run_server()."""
        logger.info(
            "chat_escalation_ingestion_loop_started",
            tick_seconds=self._tick_seconds,
        )
        while True:
            try:
                await self.tick_once()
            except Exception:
                logger.exception("chat_escalation_ingestion_loop_tick_failed")
            await asyncio.sleep(self._tick_seconds)

    async def tick_once(self, *, now: datetime | None = None) -> int:
        """One ingestion pass. Returns the number of rows processed.

        Exposed for unit tests so they can drive the loop a single tick
        at a time without any sleep.
        """
        ts = now or datetime.now(tz=UTC)
        conn = self._db.connection
        cursor = await conn.execute(
            """
            SELECT id, correlation_id, user_id, task_id, result, iteration
              FROM escalation_request
             WHERE mode = 'chat'
               AND status = 'submitted'
               AND task_id IS NOT NULL
             ORDER BY submitted_at ASC
            """,
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        processed = 0
        for raw in rows:
            record = dict(zip(cols, raw, strict=True))
            try:
                if await self._apply_one(conn=conn, record=record, now=ts):
                    processed += 1
            except Exception:
                logger.exception(
                    "chat_escalation_ingestion_failed",
                    correlation_id=record.get("correlation_id"),
                    escalation_request_id=record.get("id"),
                )
        return processed

    async def _apply_one(
        self,
        *,
        conn: aiosqlite.Connection,
        record: dict[str, Any],
        now: datetime,
    ) -> bool:
        correlation_id = str(record["correlation_id"])
        task_id = record["task_id"]
        if task_id is None:
            return False

        answer = self._parse_answer(record["result"])
        if answer is None:
            logger.warning(
                "chat_escalation_ingestion_no_answer",
                correlation_id=correlation_id,
            )
            return False

        annotated = f"[escalation:{correlation_id}] {answer}"
        existing_notes = await self._read_notes(conn=conn, task_id=str(task_id))
        # Persist the annotated notes first, then transition to DONE through the
        # state machine. The ``*→done`` wildcard in ``config/task_states.yaml``
        # now makes "complete from anywhere" a legal, validated transition, so
        # every status write (including this chat-mode ingestion path) goes
        # through lock+validate+emit and fires the completion side-effects.
        await self._db.update_task(
            str(task_id),
            notes=[*existing_notes, annotated],
        )
        try:
            await self._db.transition_task_state(str(task_id), TaskStatus.DONE)
        except InvalidTransitionError:
            # Legal for every non-cancelled state; a cancelled task remains
            # blocked by design. Log and continue — the notes annotation is
            # already applied and the escalation row is still marked below.
            logger.warning(
                "chat_escalation_ingestion_transition_rejected",
                correlation_id=correlation_id,
                task_id=str(task_id),
            )

        validation_result = json.dumps(
            {
                "channel": "chat",
                "ingested": True,
                "correlation_id": correlation_id,
            }
        )
        update_cursor = await conn.execute(
            """
            UPDATE escalation_request
               SET status = 'validated',
                   validated_at = ?,
                   validation_result = ?
             WHERE id = ?
               AND status = 'submitted'
            """,
            (now.isoformat(), validation_result, int(record["id"])),
        )
        await conn.commit()
        if update_cursor.rowcount == 0:
            # Lost the race — another writer flipped the row first.
            return False

        await write_escalation_event(
            conn,
            event=EVENT_VALIDATED,
            escalation_request_id=int(record["id"]),
            correlation_id=correlation_id,
            user_id=str(record["user_id"]),
            task_id=str(task_id),
            payload={
                "channel": "chat",
                "iteration": int(record["iteration"]),
                "answer_chars": len(answer),
            },
            now=now,
        )
        logger.info(
            "chat_escalation_ingested",
            correlation_id=correlation_id,
            task_id=task_id,
            answer_chars=len(answer),
        )
        return True

    @staticmethod
    def _parse_answer(raw: Any) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, dict):
            payload = raw
        else:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return None
        if not isinstance(payload, dict):
            return None
        if payload.get("mode") != "chat":
            return None
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return None
        return answer

    async def _read_notes(
        self, *, conn: aiosqlite.Connection, task_id: str
    ) -> list[str]:
        cursor = await conn.execute(
            "SELECT notes FROM tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return []
        raw = row[0]
        if not raw:
            return []
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(n) for n in parsed]


__all__ = [
    "DEFAULT_TICK_SECONDS",
    "EVENT_VALIDATED",
    "ChatEscalationIngestionPoller",
]
