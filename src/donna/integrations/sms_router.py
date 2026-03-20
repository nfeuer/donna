"""Inbound SMS conversation routing for Donna.

Routes inbound SMS messages to the correct handler:
  - Active context found → append response to that conversation
  - Multiple active contexts → send disambiguation SMS
  - No active context → parse as new task via InputParser

Conversation contexts use a sliding 24h TTL (expires_at) with an absolute
72h hard cap (hard_expires_at) from creation. Both are enforced here.

See slices/slice_07_sms_escalation.md and docs/notifications.md.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import structlog

from donna.config import SmsConfig

logger = structlog.get_logger()


@dataclasses.dataclass
class SmsContext:
    """Projection of an active conversation_context row."""

    id: str
    task_id: str
    task_title: str
    agent_id: str
    expires_at: datetime
    hard_expires_at: datetime | None


class SmsRouter:
    """Routes inbound SMS to the appropriate handler.

    Usage:
        router = SmsRouter(db, input_parser, sms, sms_config, user_id, user_phone)
        await router.route_inbound(from_number="+15555555555", body="buy milk")
    """

    def __init__(
        self,
        db: object,
        input_parser: object,
        sms: object,
        sms_config: SmsConfig,
        user_id: str,
        user_phone: str,
    ) -> None:
        self._db = db  # donna.tasks.database.Database
        self._input_parser = input_parser  # donna.orchestrator.input_parser.InputParser
        self._sms = sms  # donna.integrations.twilio_sms.TwilioSMS
        self._config = sms_config
        self._user_id = user_id
        self._user_phone = user_phone

    async def route_inbound(self, from_number: str, body: str) -> None:
        """Route an inbound SMS to the correct handler.

        Args:
            from_number: E.164 number the SMS was received from.
            body: Raw SMS body text.
        """
        log = logger.bind(from_number=from_number, user_id=self._user_id)

        # Verify sender is the known user.
        if from_number != self._user_phone:
            log.warning("sms_inbound_unknown_sender")
            return

        body_stripped = body.strip()

        # Expire stale contexts before routing.
        await self._expire_stale_contexts()

        contexts = await self._get_active_contexts()

        if not contexts:
            # No active context — new task input.
            log.info("sms_inbound_new_task", body_len=len(body_stripped))
            await self._parse_new_task(body_stripped)

        elif len(contexts) == 1:
            # Single context — route reply to that agent.
            ctx = contexts[0]
            log.info("sms_inbound_routed", task_id=ctx.task_id, agent_id=ctx.agent_id)
            await self._append_response(ctx, body_stripped)

        else:
            # Multiple contexts — send disambiguation.
            log.info("sms_inbound_disambiguate", context_count=len(contexts))
            await self._disambiguate(contexts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_active_contexts(self) -> list[SmsContext]:
        """Query conversation_context for active SMS contexts for this user."""
        conn = self._db.connection  # type: ignore[attr-defined]
        now = datetime.now(tz=UTC).isoformat()
        cursor = await conn.execute(
            """
            SELECT cc.id, cc.task_id, COALESCE(t.title, cc.task_id),
                   cc.agent_id, cc.expires_at, cc.hard_expires_at
              FROM conversation_context cc
              LEFT JOIN tasks t ON t.id = cc.task_id
             WHERE cc.user_id = ?
               AND cc.channel = 'sms'
               AND cc.status = 'active'
               AND cc.expires_at > ?
             ORDER BY cc.last_activity DESC
            """,
            (self._user_id, now),
        )
        rows = await cursor.fetchall()
        return [_row_to_context(r) for r in rows]

    async def _expire_stale_contexts(self) -> None:
        """Mark contexts expired if past expires_at or hard_expires_at."""
        conn = self._db.connection  # type: ignore[attr-defined]
        now = datetime.now(tz=UTC).isoformat()
        await conn.execute(
            """
            UPDATE conversation_context
               SET status = 'expired'
             WHERE user_id = ?
               AND channel = 'sms'
               AND status = 'active'
               AND (expires_at <= ? OR (hard_expires_at IS NOT NULL AND hard_expires_at <= ?))
            """,
            (self._user_id, now, now),
        )
        await conn.commit()

    async def _append_response(self, ctx: SmsContext, body: str) -> None:
        """Append user reply to context and slide the TTL."""
        conn = self._db.connection  # type: ignore[attr-defined]
        now = datetime.now(tz=UTC)
        new_expires = (now + timedelta(hours=self._config.conversation_context.sliding_ttl_hours)).isoformat()

        # Fetch current responses_received and append.
        cursor = await conn.execute(
            "SELECT responses_received FROM conversation_context WHERE id = ?",
            (ctx.id,),
        )
        row = await cursor.fetchone()
        import json
        existing = json.loads(row[0]) if row and row[0] else []
        existing.append({"text": body, "at": now.isoformat()})

        await conn.execute(
            """
            UPDATE conversation_context
               SET responses_received = ?, last_activity = ?, expires_at = ?
             WHERE id = ?
            """,
            (json.dumps(existing), now.isoformat(), new_expires, ctx.id),
        )
        await conn.commit()
        logger.info(
            "sms_context_response_appended",
            context_id=ctx.id,
            task_id=ctx.task_id,
            agent_id=ctx.agent_id,
        )

    async def _parse_new_task(self, body: str) -> None:
        """Dispatch body to InputParser as a new SMS task."""
        try:
            await self._input_parser.parse(  # type: ignore[attr-defined]
                raw_text=body,
                user_id=self._user_id,
                channel="sms",
            )
        except Exception:
            logger.exception("sms_inbound_parse_failed", user_id=self._user_id)

    async def _disambiguate(self, contexts: list[SmsContext]) -> None:
        """Send a disambiguation SMS listing active tasks."""
        lines = ["Which task are you replying about?"]
        for i, ctx in enumerate(contexts, start=1):
            lines.append(f"({i}) {ctx.task_title}")
        message = "\n".join(lines)

        sent = await self._sms.send(to=self._user_phone, body=message)  # type: ignore[attr-defined]
        if not sent:
            logger.warning(
                "sms_disambiguation_not_sent",
                user_id=self._user_id,
                context_count=len(contexts),
            )


def _row_to_context(row: tuple) -> SmsContext:  # type: ignore[type-arg]
    return SmsContext(
        id=row[0],
        task_id=row[1],
        task_title=row[2],
        agent_id=row[3],
        expires_at=_parse_dt(row[4]),
        hard_expires_at=_parse_dt(row[5]) if row[5] else None,
    )


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
