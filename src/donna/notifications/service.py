"""Central notification dispatch service for Donna.

All outbound notifications flow through this service. It enforces
blackout and quiet-hour rules, queues blocked notifications for later
delivery, and logs every dispatch attempt.

Blackout (12 AM–6 AM): hard block, all priorities queued.
Quiet hours (8 PM–midnight): soft block, priority < 5 queued.

See docs/notifications.md and slices/slice_05_reminders_digest.md.
"""

from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import discord
import structlog

from donna.config import CalendarConfig
from donna.notifications.bot_protocol import BotProtocol

if TYPE_CHECKING:
    from donna.integrations.gmail import GmailClient
    from donna.integrations.twilio_sms import TwilioSMS

logger = structlog.get_logger()

# Notification type constants
NOTIF_REMINDER = "reminder"
NOTIF_OVERDUE = "overdue"
NOTIF_DIGEST = "digest"
NOTIF_DEBUG = "debug"
NOTIF_AUTOMATION_ALERT = "automation_alert"
NOTIF_AUTOMATION_FAILURE = "automation_failure"

# Channel name constants
CHANNEL_TASKS = "tasks"
CHANNEL_DIGEST = "digest"
CHANNEL_DEBUG = "debug"


class NotificationService:
    """Central dispatch for all outbound Donna notifications.

    Routes messages to Discord channels via DonnaBot, enforcing time-window
    rules loaded from CalendarConfig. Queued notifications are replayed
    by calling flush_queue() when the blackout window ends.
    """

    def __init__(
        self,
        bot: BotProtocol,
        calendar_config: CalendarConfig,
        user_id: str,
        sms: "TwilioSMS | None" = None,
        gmail: "GmailClient | None" = None,
    ) -> None:
        self._bot = bot
        self._tw = calendar_config.time_windows
        self._user_id = user_id
        self._sms = sms
        self._gmail = gmail
        # Queue of async callables to replay after blackout ends.
        self._queue: deque[Callable[[], Awaitable[None]]] = deque()

    def _is_blackout(self, now: datetime) -> bool:
        """Return True if current time is within the absolute blackout window."""
        hour = now.hour
        start = self._tw.blackout.start_hour  # 0
        end = self._tw.blackout.end_hour      # 6
        return start <= hour < end

    def _is_quiet(self, now: datetime) -> bool:
        """Return True if current time is within quiet hours (8 PM–midnight)."""
        hour = now.hour
        start = self._tw.quiet_hours.start_hour  # 20
        end = self._tw.quiet_hours.end_hour       # 24 (midnight)
        return start <= hour < end

    async def dispatch(
        self,
        notification_type: str,
        content: str,
        channel: str = CHANNEL_TASKS,
        priority: int = 2,
        embed: discord.Embed | None = None,
        thread_id: int | None = None,
    ) -> bool:
        """Dispatch a notification, respecting blackout and quiet hours.

        Args:
            notification_type: One of NOTIF_* constants.
            content: Message text.
            channel: One of CHANNEL_* constants.
            priority: 1–5; only priority 5 passes through quiet hours.
            embed: Optional Discord embed (e.g. for digest).
            thread_id: If set, sends to an existing thread instead of channel.

        Returns:
            True if the message was sent immediately, False if queued/blocked.
        """
        now = datetime.now(tz=timezone.utc)
        content_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:8]

        log = logger.bind(
            notification_type=notification_type,
            channel=channel,
            priority=priority,
            content_hash=content_hash,
            user_id=self._user_id,
        )

        # Hard block: blackout applies to all priorities.
        if self._is_blackout(now):
            log.info("notification_queued_blackout")
            self._enqueue(notification_type, content, channel, priority, embed, thread_id)
            return False

        # Soft block: quiet hours apply to priority < 5.
        if self._is_quiet(now) and priority < 5:
            log.info("notification_queued_quiet_hours")
            self._enqueue(notification_type, content, channel, priority, embed, thread_id)
            return False

        await self._send(notification_type, content, channel, embed, thread_id, log)
        return True

    def _enqueue(
        self,
        notification_type: str,
        content: str,
        channel: str,
        priority: int,
        embed: discord.Embed | None,
        thread_id: int | None,
    ) -> None:
        """Add a send coroutine to the deferred queue."""
        async def _send_later() -> None:
            log = logger.bind(
                notification_type=notification_type,
                channel=channel,
                user_id=self._user_id,
            )
            await self._send(notification_type, content, channel, embed, thread_id, log)

        self._queue.append(_send_later)

    async def flush_queue(self) -> int:
        """Replay all queued notifications. Called at blackout boundary (6 AM).

        Returns:
            Number of notifications flushed.
        """
        flushed = 0
        while self._queue:
            send_fn = self._queue.popleft()
            try:
                await send_fn()
                flushed += 1
            except Exception:
                logger.exception("notification_flush_failed")
        if flushed:
            logger.info("notification_queue_flushed", count=flushed)
        return flushed

    async def dispatch_sms(
        self,
        body: str,
        to: str,
        priority: int = 2,
    ) -> bool:
        """Dispatch an outbound SMS, respecting blackout and quiet hours.

        Args:
            body: SMS message body.
            to: Destination E.164 phone number.
            priority: 1–5; priority < 5 is blocked during quiet hours.

        Returns:
            True if SMS was sent, False if blocked or SMS not configured.
        """
        if self._sms is None:
            logger.warning("sms_dispatch_skipped_no_client", user_id=self._user_id)
            return False

        now = datetime.now(tz=timezone.utc)

        if self._is_blackout(now):
            logger.info("sms_blocked_blackout", user_id=self._user_id)
            return False

        if self._is_quiet(now) and priority < 5:
            logger.info("sms_blocked_quiet_hours", user_id=self._user_id)
            return False

        return await self._sms.send(to=to, body=body)

    async def dispatch_email(
        self,
        to: str,
        subject: str,
        body: str,
        priority: int = 2,
    ) -> bool:
        """Create an outbound email draft, respecting blackout and quiet hours.

        Drafts are created via GmailClient.create_draft() — never sent directly.
        The user must approve sending separately (or send_enabled must be True).

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Plain-text email body.
            priority: 1–5; priority < 5 is blocked during quiet hours.

        Returns:
            True if the draft was created, False if blocked or Gmail not configured.
        """
        if self._gmail is None:
            logger.warning("email_dispatch_skipped_no_client", user_id=self._user_id)
            return False

        now = datetime.now(tz=timezone.utc)

        if self._is_blackout(now):
            logger.info("email_blocked_blackout", user_id=self._user_id)
            return False

        if self._is_quiet(now) and priority < 5:
            logger.info("email_blocked_quiet_hours", user_id=self._user_id)
            return False

        try:
            await self._gmail.create_draft(to=to, subject=subject, body=body)
            logger.info(
                "email_draft_created",
                to=to,
                subject=subject,
                user_id=self._user_id,
            )
            return True
        except Exception:
            logger.exception("email_draft_failed", to=to, user_id=self._user_id)
            return False

    async def _send(
        self,
        notification_type: str,
        content: str,
        channel: str,
        embed: discord.Embed | None,
        thread_id: int | None,
        log: Any,
    ) -> None:
        """Execute the actual Discord send and log the outcome."""
        try:
            if thread_id is not None:
                await self._bot.send_to_thread(thread_id, content)
            elif embed is not None:
                await self._bot.send_embed(channel, embed)
            else:
                await self._bot.send_message(channel, content)

            log.info("notification_sent")
        except Exception:
            log.exception("notification_send_failed")
