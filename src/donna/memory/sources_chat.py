"""ChatSource — index conversation turns into the memory store.

Hooks into :meth:`donna.tasks.database.Database.add_chat_message` via
the Option A constructor callback (see
:mod:`donna.memory.observers` for the wiring rationale). The source
maintains a per-session rolling buffer and flushes a turn document
when:

1. the role flips (user → assistant or vice versa);
2. the buffer reaches :attr:`ChatTurnChunker.max_tokens`; or
3. the session transitions out of ``active`` (close / expire).

``source_id`` is ``"{session_id}:{first_msg_id}-{last_msg_id}"`` so an
updated buffer upserts the same document (idempotent by
``UNIQUE(user_id, source_type, source_id)``). The backfill path
walks existing ``conversation_messages`` + ``conversation_sessions``
rows and regroups them through the same chunker; running backfill
twice is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from donna.config import ChatSourceConfig
from donna.memory.chunking import ChatTurn, ChatTurnChunker
from donna.memory.store import Document, MemoryStore

logger = structlog.get_logger()

SOURCE_TYPE = "chat"


@dataclass
class _SessionBuffer:
    user_id: str
    role: str | None = None
    messages: list[dict[str, Any]] | None = None

    def reset(self) -> None:
        self.role = None
        self.messages = []


class ChatSource:
    """Observer + backfill for chat turns.

    Stateless from the DB's perspective: the only side effect is
    ``MemoryStore.upsert`` (via the ingest queue if available, else
    direct). Failures are logged under ``memory_ingest_failed`` and
    swallowed — the caller has already committed the chat row.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        cfg: ChatSourceConfig,
        user_id_default: str = "nick",
    ) -> None:
        self._store = store
        self._cfg = cfg
        self._chunker = ChatTurnChunker(
            max_tokens=256,
            merge_consecutive_roles=cfg.merge_consecutive_same_role,
            min_chars=cfg.min_chars,
            task_verbs=list(cfg.task_verbs),
            include_roles=list(cfg.index_roles),
        )
        self._default_user_id = user_id_default
        # Per-session rolling buffer (role + message list). Flushed
        # whenever the chunker would close a turn.
        self._buffers: dict[str, _SessionBuffer] = {}

    # -- observer -----------------------------------------------------

    async def observe_message(self, event: dict[str, Any]) -> None:
        """Handle one ``chat_message_added`` event.

        Event shape: ``{"session_id", "user_id", "message": {...}}``.
        """
        if not self._cfg.enabled:
            return
        msg = event.get("message") or {}
        role = str(msg.get("role") or "")
        if role not in self._cfg.index_roles:
            # Still flush — a system message creates a turn boundary.
            await self._flush_session(
                event["session_id"],
                user_id=str(event.get("user_id") or self._default_user_id),
            )
            return
        session_id = str(event["session_id"])
        user_id = str(event.get("user_id") or self._default_user_id)
        buf = self._buffers.setdefault(
            session_id, _SessionBuffer(user_id=user_id, messages=[])
        )
        if buf.messages is None:
            buf.messages = []
        if buf.role is not None and role != buf.role:
            await self._flush_session(session_id, user_id=user_id)
            buf = self._buffers.setdefault(
                session_id, _SessionBuffer(user_id=user_id, messages=[])
            )
            if buf.messages is None:
                buf.messages = []
        buf.role = role
        buf.messages.append(
            {"id": str(msg["id"]), "role": role, "content": msg.get("content") or ""}
        )
        # Re-chunk the buffer: if the chunker emits more than one turn
        # the earlier ones are complete and ready to flush.
        turns = self._chunker.chunk_messages(buf.messages)
        if len(turns) > 1:
            for turn in turns[:-1]:
                await self._emit_turn(session_id, user_id, turn)
            # Keep only the messages that contribute to the last turn
            # (they may still grow on the next incoming message).
            tail_ids = set(turns[-1].message_ids)
            buf.messages = [m for m in buf.messages if m["id"] in tail_ids]
        elif len(turns) == 1 and turns[0].token_count >= self._chunker.max_tokens:
            await self._emit_turn(session_id, user_id, turns[0])
            buf.reset()

    async def observe_session_closed(self, event: dict[str, Any]) -> None:
        """Flush the session buffer on EXPIRED / CLOSED transitions."""
        if not self._cfg.enabled:
            return
        session_id = str(event["session_id"])
        user_id = str(event.get("user_id") or self._default_user_id)
        await self._flush_session(session_id, user_id=user_id)
        self._buffers.pop(session_id, None)

    # -- backfill -----------------------------------------------------

    async def backfill(self, user_id: str) -> int:
        """Re-ingest every chat session for ``user_id``.

        Walks ``conversation_sessions`` + ``conversation_messages``,
        regroups into turns via the chunker, and upserts each. The
        upsert is idempotent on ``(user_id, source_type, source_id)``
        so re-running leaves row counts unchanged.
        """
        if not self._cfg.enabled:
            return 0
        conn = self._store._conn  # type: ignore[attr-defined]  # intentional — same pkg
        async with conn.execute(
            "SELECT id FROM conversation_sessions WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ) as cur:
            session_rows = await cur.fetchall()
        n = 0
        for (session_id,) in session_rows:
            async with conn.execute(
                "SELECT id, role, content FROM conversation_messages "
                "WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            ) as mcur:
                msg_rows = await mcur.fetchall()
            messages = [
                {"id": mid, "role": role, "content": content or ""}
                for (mid, role, content) in msg_rows
            ]
            turns = self._chunker.chunk_messages(messages)
            for turn in turns:
                try:
                    await self._upsert_turn(session_id, user_id, turn)
                    n += 1
                except Exception as exc:
                    logger.warning(
                        "memory_ingest_failed",
                        source_type=SOURCE_TYPE,
                        reason=str(exc),
                        session_id=session_id,
                    )
        logger.info("memory_backfill_chat_done", count=n, user_id=user_id)
        return n

    # -- internals ----------------------------------------------------

    async def _flush_session(self, session_id: str, *, user_id: str) -> None:
        buf = self._buffers.get(session_id)
        if buf is None or not buf.messages:
            return
        turns = self._chunker.chunk_messages(buf.messages)
        for turn in turns:
            await self._emit_turn(session_id, user_id, turn)
        buf.reset()

    async def _emit_turn(
        self, session_id: str, user_id: str, turn: ChatTurn
    ) -> None:
        try:
            await self._upsert_turn(session_id, user_id, turn)
            logger.info(
                "memory_ingest_chat_turn",
                session_id=session_id,
                role=turn.role,
                first_msg_id=turn.first_msg_id,
                last_msg_id=turn.last_msg_id,
                tokens=turn.token_count,
            )
        except Exception as exc:
            logger.warning(
                "memory_ingest_failed",
                source_type=SOURCE_TYPE,
                reason=str(exc),
                session_id=session_id,
            )

    async def _upsert_turn(
        self, session_id: str, user_id: str, turn: ChatTurn
    ) -> str:
        source_id = f"{session_id}:{turn.first_msg_id}-{turn.last_msg_id}"
        doc = Document(
            user_id=user_id,
            source_type=SOURCE_TYPE,
            source_id=source_id,
            title=f"Chat {turn.role} turn",
            uri=f"chat:{session_id}",
            content=turn.content,
            metadata={
                "session_id": session_id,
                "role": turn.role,
                "first_msg_id": turn.first_msg_id,
                "last_msg_id": turn.last_msg_id,
                "message_ids": list(turn.message_ids),
            },
        )
        return await self._store.upsert(doc)


__all__ = ["SOURCE_TYPE", "ChatSource"]
