"""Notification escalation tier state machine for Donna.

When a task goes overdue and the user doesn't respond, Donna escalates:
  Tier 1: Discord message → wait 30 min (configurable)
  Tier 2: SMS text       → wait 1 hour  (configurable)
  Tier 3: Email          → deferred to Slice 8
  Tier 4: Phone TTS      → deferred (priority 5 / budget emergencies only)

Escalation state is persisted in the `escalation_state` SQLite table.
Acknowledgment on any channel resets the escalation.
"Busy" reply backs off escalation for a configurable number of hours.

See slices/slice_07_sms_escalation.md and docs/notifications.md.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
import uuid6

from donna.config import SmsConfig
from donna.notifications.service import CHANNEL_TASKS, NOTIF_OVERDUE, NotificationService

if TYPE_CHECKING:
    from donna.integrations.gmail import GmailClient
    from donna.integrations.twilio_sms import TwilioSMS
    from donna.integrations.twilio_voice import TwilioVoice
    from donna.tasks.database import Database

logger = structlog.get_logger()

CHECK_INTERVAL_SECONDS = 300  # 5 minutes

# Escalation status values
STATUS_PENDING = "pending"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_BACKED_OFF = "backed_off"
STATUS_COMPLETED = "completed"


@dataclasses.dataclass
class EscalationState:
    """In-memory projection of an escalation_state row."""

    id: str
    user_id: str
    task_id: str
    task_title: str
    current_tier: int
    status: str
    next_escalation_at: datetime
    created_at: datetime
    updated_at: datetime


class EscalationManager:
    """Manages escalation tiers for overdue task nudges.

    Usage:
        manager = EscalationManager(db, service, sms, sms_config, user_id, user_phone)
        await manager.escalate(task_id, task_title, nudge_text, priority)
        asyncio.create_task(manager.check_and_advance())
    """

    def __init__(
        self,
        db: Database,
        service: NotificationService,
        sms: TwilioSMS,
        sms_config: SmsConfig,
        user_id: str,
        user_phone: str,
        gmail: GmailClient | None = None,
        user_email: str = "",
        voice: TwilioVoice | None = None,
        tier4_enabled: bool = False,
    ) -> None:
        self._db = db
        self._service = service
        self._sms = sms
        self._config = sms_config
        self._user_id = user_id
        self._user_phone = user_phone
        self._gmail = gmail
        self._user_email = user_email
        self._voice = voice
        self._tier4_enabled = tier4_enabled

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def escalate(
        self,
        task_id: str,
        task_title: str,
        nudge_text: str,
        priority: int = 2,
        start_at_tier: int = 1,
    ) -> None:
        """Start or continue escalation for an overdue task.

        If a pending escalation already exists for this task, this is a no-op
        (check_and_advance handles advancement). Otherwise, sends Tier 1
        (Discord) and records the escalation.

        Args:
            task_id: The overdue task's ID.
            task_title: Human-readable task title (for SMS text).
            nudge_text: The notification message body.
            priority: Task priority (1–5).
            start_at_tier: Starting tier (default 1; use 2 for budget alerts).
        """
        existing = await self._get_pending(task_id)
        if existing is not None:
            logger.info(
                "escalation_already_pending",
                task_id=task_id,
                current_tier=existing.current_tier,
            )
            return

        now = datetime.now(tz=UTC)

        if start_at_tier == 1:
            # Tier 1: Discord
            await self._service.dispatch(
                notification_type=NOTIF_OVERDUE,
                content=nudge_text,
                channel=CHANNEL_TASKS,
                priority=priority,
            )
            wait = timedelta(minutes=self._config.escalation.tier1_wait_minutes)
            current_tier = 1
        else:
            # Skip to SMS immediately (e.g. budget alerts)
            await self._send_sms(task_title, nudge_text)
            wait = timedelta(minutes=self._config.escalation.tier2_wait_minutes)
            current_tier = 2

        next_at = now + wait
        await self._insert_state(task_id, task_title, current_tier, STATUS_PENDING, next_at, now)

        logger.info(
            "escalation_started",
            task_id=task_id,
            tier=current_tier,
            next_escalation_at=next_at.isoformat(),
            user_id=self._user_id,
        )

    async def check_and_advance(self) -> None:
        """Background loop: advance pending escalations past their wait time.

        Runs every CHECK_INTERVAL_SECONDS (5 min).
        """
        logger.info("escalation_checker_started", interval_seconds=CHECK_INTERVAL_SECONDS)
        while True:
            try:
                await self._advance_due()
            except Exception:
                logger.exception("escalation_advance_failed")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def acknowledge(self, task_id: str) -> None:
        """Reset escalation on user acknowledgment (done/reschedule on any channel).

        Args:
            task_id: The task being acknowledged.
        """
        conn = self._db.connection
        now = datetime.now(tz=UTC).isoformat()
        await conn.execute(
            """
            UPDATE escalation_state
               SET status = ?, updated_at = ?
             WHERE task_id = ? AND user_id = ? AND status IN (?, ?)
            """,
            (STATUS_ACKNOWLEDGED, now, task_id, self._user_id, STATUS_PENDING, STATUS_BACKED_OFF),
        )
        await conn.commit()
        logger.info("escalation_acknowledged", task_id=task_id, user_id=self._user_id)

    async def backoff(self, task_id: str) -> None:
        """Back off escalation by busy_backoff_hours when user replies "busy".

        Args:
            task_id: The task to back off.
        """
        conn = self._db.connection
        now = datetime.now(tz=UTC)
        new_next = now + timedelta(hours=self._config.escalation.busy_backoff_hours)
        await conn.execute(
            """
            UPDATE escalation_state
               SET status = ?, next_escalation_at = ?, updated_at = ?
             WHERE task_id = ? AND user_id = ? AND status IN (?, ?)
            """,
            (
                STATUS_BACKED_OFF,
                new_next.isoformat(),
                now.isoformat(),
                task_id,
                self._user_id,
                STATUS_PENDING,
                STATUS_BACKED_OFF,
            ),
        )
        await conn.commit()
        logger.info(
            "escalation_backed_off",
            task_id=task_id,
            user_id=self._user_id,
            next_escalation_at=new_next.isoformat(),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _advance_due(self) -> None:
        """Query and advance all escalations past their next_escalation_at."""
        conn = self._db.connection
        now = datetime.now(tz=UTC)
        cursor = await conn.execute(
            """
            SELECT id, user_id, task_id, task_title, current_tier, status,
                   next_escalation_at, created_at, updated_at
              FROM escalation_state
             WHERE user_id = ?
               AND status IN (?, ?)
               AND next_escalation_at <= ?
            """,
            (self._user_id, STATUS_PENDING, STATUS_BACKED_OFF, now.isoformat()),
        )
        rows = await cursor.fetchall()

        for row in rows:
            state = _row_to_state(row)
            await self._advance_one(state, now)

    async def _advance_one(self, state: EscalationState, now: datetime) -> None:
        """Advance a single escalation to the next tier."""
        next_tier = state.current_tier + 1

        if next_tier == 2:
            # Tier 2: SMS
            nudge = f"Still waiting on '{state.task_title}'. Please respond."
            await self._send_sms(state.task_title, nudge)
            wait = timedelta(minutes=self._config.escalation.tier2_wait_minutes)
            next_at = now + wait
            await self._update_state(state.id, next_tier, STATUS_PENDING, next_at, now)
            logger.info(
                "escalation_advanced",
                task_id=state.task_id,
                from_tier=state.current_tier,
                to_tier=next_tier,
                user_id=self._user_id,
            )
        elif next_tier == 3:
            # Tier 3: Email with "ACTION REQUIRED" subject
            nudge = f"Still waiting on '{state.task_title}'. Please respond."
            await self._send_email_escalation(state.task_title, nudge)
            wait = timedelta(minutes=self._config.escalation.tier3_wait_minutes)
            next_at = now + wait
            await self._update_state(state.id, next_tier, STATUS_PENDING, next_at, now)
            logger.info(
                "escalation_advanced",
                task_id=state.task_id,
                from_tier=state.current_tier,
                to_tier=next_tier,
                user_id=self._user_id,
            )
        elif next_tier == 4:
            # Tier 4: TTS phone call (priority 5 / budget emergencies only)
            if self._tier4_enabled and self._voice is not None:
                nudge = f"Urgent from Donna: {state.task_title} requires your attention."
                called = await self._voice.call(to=self._user_phone, message=nudge)
                if called:
                    logger.info(
                        "escalation_advanced",
                        task_id=state.task_id,
                        from_tier=state.current_tier,
                        to_tier=next_tier,
                        user_id=self._user_id,
                    )
                else:
                    logger.warning(
                        "escalation_voice_call_failed",
                        task_id=state.task_id,
                        user_id=self._user_id,
                    )
            else:
                logger.info(
                    "escalation_tier4_disabled",
                    task_id=state.task_id,
                    tier4_enabled=self._tier4_enabled,
                    voice_configured=self._voice is not None,
                    user_id=self._user_id,
                )
            # Tier 4 is the final tier — mark completed regardless.
            await self._update_state(state.id, next_tier, STATUS_COMPLETED, now, now)
        else:
            # Beyond Tier 4 — stop escalating.
            await self._update_state(state.id, state.current_tier, STATUS_COMPLETED, now, now)
            logger.info(
                "escalation_max_tier_reached",
                task_id=state.task_id,
                tier=state.current_tier,
                user_id=self._user_id,
            )

    async def _send_sms(self, task_title: str, nudge_text: str) -> None:
        """Send SMS escalation nudge."""
        body = f"[Donna] {nudge_text}"
        sent = await self._sms.send(to=self._user_phone, body=body)
        if not sent:
            logger.warning(
                "escalation_sms_not_sent",
                task_title=task_title,
                user_id=self._user_id,
            )

    async def _send_email_escalation(self, task_title: str, nudge_text: str) -> None:
        """Send Tier 3 email escalation draft with ACTION REQUIRED subject."""
        if not self._user_email:
            logger.warning(
                "escalation_email_skipped_no_address",
                task_title=task_title,
                user_id=self._user_id,
            )
            return

        subject = f"ACTION REQUIRED: {task_title}"
        body = f"[Donna]\n\n{nudge_text}\n\nPlease acknowledge or reschedule this task."
        sent = await self._service.dispatch_email(
            to=self._user_email,
            subject=subject,
            body=body,
            priority=5,  # Escalation emails bypass quiet hours.
        )
        if not sent:
            logger.warning(
                "escalation_email_not_sent",
                task_title=task_title,
                user_id=self._user_id,
            )

    async def _get_pending(self, task_id: str) -> EscalationState | None:
        """Return the pending/backed-off escalation for a task, or None."""
        conn = self._db.connection
        cursor = await conn.execute(
            """
            SELECT id, user_id, task_id, task_title, current_tier, status,
                   next_escalation_at, created_at, updated_at
              FROM escalation_state
             WHERE task_id = ? AND user_id = ? AND status IN (?, ?)
             LIMIT 1
            """,
            (task_id, self._user_id, STATUS_PENDING, STATUS_BACKED_OFF),
        )
        row = await cursor.fetchone()
        return _row_to_state(row) if row else None

    async def _insert_state(
        self,
        task_id: str,
        task_title: str,
        tier: int,
        status: str,
        next_at: datetime,
        now: datetime,
    ) -> None:
        conn = self._db.connection
        esc_id = str(uuid6.uuid7())
        await conn.execute(
            """
            INSERT INTO escalation_state
              (id, user_id, task_id, task_title, current_tier, status,
               next_escalation_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                esc_id,
                self._user_id,
                task_id,
                task_title,
                tier,
                status,
                next_at.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await conn.commit()

    async def _update_state(
        self,
        esc_id: str,
        tier: int,
        status: str,
        next_at: datetime,
        now: datetime,
    ) -> None:
        conn = self._db.connection
        await conn.execute(
            """
            UPDATE escalation_state
               SET current_tier = ?, status = ?, next_escalation_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (tier, status, next_at.isoformat(), now.isoformat(), esc_id),
        )
        await conn.commit()


def _row_to_state(row: tuple) -> EscalationState:  # type: ignore[type-arg]
    """Map a raw SQLite row to an EscalationState."""
    return EscalationState(
        id=row[0],
        user_id=row[1],
        task_id=row[2],
        task_title=row[3],
        current_tier=row[4],
        status=row[5],
        next_escalation_at=_parse_dt(row[6]),
        created_at=_parse_dt(row[7]),
        updated_at=_parse_dt(row[8]),
    )


def _parse_dt(value: str) -> datetime:
    """Parse ISO datetime string to UTC-aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
