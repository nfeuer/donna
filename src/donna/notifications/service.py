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
import zoneinfo
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord
import structlog

from donna.config import CalendarConfig, NotificationPolicyConfig
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

# Discord message length limits
DIGEST_MAX_CHARS_DEFAULT = 1900
DIGEST_HARD_CEILING = 2000  # Discord message limit

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
        sms: TwilioSMS | None = None,
        gmail: GmailClient | None = None,
        digest_max_chars: int = DIGEST_MAX_CHARS_DEFAULT,
        notification_policy: NotificationPolicyConfig | None = None,
    ) -> None:
        self._bot = bot
        self._tw = calendar_config.time_windows
        self._tz = zoneinfo.ZoneInfo(calendar_config.timezone)
        self._user_id = user_id
        self._sms = sms
        self._gmail = gmail
        self._digest_max_chars = digest_max_chars
        # Queue of async callables to replay after blackout ends.
        self._queue: deque[Callable[[], Awaitable[None]]] = deque()
        # Rate-limiting state for fallback alerts: (component, error_prefix) → last sent.
        self._fallback_alert_history: dict[tuple[str, str], datetime] = {}
        self._alerting = False
        # Per-type window exemptions. Empty sets => every type respects both
        # windows (legacy behavior when no policy is supplied).
        self._blackout_exempt: set[str] = (
            set(notification_policy.blackout_exempt) if notification_policy else set()
        )
        self._quiet_exempt: set[str] = (
            set(notification_policy.quiet_exempt) if notification_policy else set()
        )

    @staticmethod
    def _truncate_for_channel(content: str, max_chars: int) -> str:
        """Cap content at max_chars. If truncated, append a count footer."""
        if len(content) <= max_chars:
            return content
        footer_budget = 64
        body_budget = max(0, max_chars - footer_budget)
        remaining = len(content) - body_budget
        return content[:body_budget] + f"\n\n…(truncated, {remaining} more chars)"

    def _local_hour(self, now: datetime) -> int:
        return now.astimezone(self._tz).hour

    def _is_blackout(self, now: datetime) -> bool:
        """Return True if current time is within the absolute blackout window."""
        hour = self._local_hour(now)
        start = self._tw.blackout.start_hour
        end = self._tw.blackout.end_hour
        return start <= hour < end

    def _is_quiet(self, now: datetime) -> bool:
        """Return True if current time is within quiet hours."""
        hour = self._local_hour(now)
        start = self._tw.quiet_hours.start_hour
        end = self._tw.quiet_hours.end_hour
        return start <= hour < end

    def _respects_blackout(self, notification_type: str) -> bool:
        return notification_type not in self._blackout_exempt

    def _respects_quiet(self, notification_type: str) -> bool:
        return notification_type not in self._quiet_exempt

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
        now = datetime.now(tz=UTC)
        if notification_type in (NOTIF_DIGEST, NOTIF_AUTOMATION_ALERT):
            content = self._truncate_for_channel(content, self._digest_max_chars)
        content_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:8]

        log = logger.bind(
            notification_type=notification_type,
            channel=channel,
            priority=priority,
            content_hash=content_hash,
            user_id=self._user_id,
        )

        # Hard block: blackout applies to all priorities.
        if self._is_blackout(now) and self._respects_blackout(notification_type):
            log.info("notification_queued_blackout")
            self._enqueue(notification_type, content, channel, priority, embed, thread_id)
            return False

        # Soft block: quiet hours apply to priority < 5.
        if self._is_quiet(now) and priority < 5 and self._respects_quiet(notification_type):
            log.info("notification_queued_quiet_hours")
            self._enqueue(notification_type, content, channel, priority, embed, thread_id)
            return False

        await self._send(notification_type, content, channel, embed, thread_id, log)
        return True

    async def dispatch_dm(
        self,
        discord_id: str,
        notification_type: str,
        content: str,
        priority: int = 2,
    ) -> bool:
        """Dispatch a direct message to a Discord user.

        Same blackout/quiet-hours gating as dispatch(). Sends via
        bot.send_dm() instead of a channel.

        Args:
            discord_id: Discord snowflake ID of the recipient.
            notification_type: One of NOTIF_* constants.
            content: Message text.
            priority: 1-5; only priority 5 passes through quiet hours.

        Returns:
            True if sent immediately, False if queued/blocked.
        """
        now = datetime.now(tz=UTC)
        content_hash = hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:8]

        log = logger.bind(
            notification_type=notification_type,
            discord_id=discord_id,
            priority=priority,
            content_hash=content_hash,
            delivery="dm",
        )

        if self._is_blackout(now) and self._respects_blackout(notification_type):
            log.info("dm_queued_blackout")
            self._enqueue_dm(discord_id, notification_type, content, priority)
            return False

        if self._is_quiet(now) and priority < 5 and self._respects_quiet(notification_type):
            log.info("dm_queued_quiet_hours")
            self._enqueue_dm(discord_id, notification_type, content, priority)
            return False

        try:
            await self._bot.send_dm(discord_id, content)
            log.info("dm_sent")
        except Exception:
            log.exception("dm_send_failed", event_type="fallback_activated")
            return False
        return True

    def _enqueue_dm(
        self,
        discord_id: str,
        notification_type: str,
        content: str,
        priority: int,
    ) -> None:
        """Add a DM send coroutine to the deferred queue."""
        async def _send_later() -> None:
            log = logger.bind(
                notification_type=notification_type,
                discord_id=discord_id,
                delivery="dm",
            )
            try:
                await self._bot.send_dm(discord_id, content)
                log.info("dm_sent_from_queue")
            except Exception:
                log.exception("dm_send_from_queue_failed")

        self._queue.append(_send_later)

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

    async def dispatch_fallback_alert(
        self,
        component: str,
        error: str,
        fallback: str,
        context: dict[str, Any] | None = None,
        cooldown_seconds: int = 3600,
    ) -> bool:
        """Send a rate-limited alert when a component activates a fallback path.

        Always logs a WARNING regardless of whether the Discord message is sent.
        Deduplicates by (component, error_prefix) within *cooldown_seconds*.

        Args:
            component: Name of the subsystem that triggered the fallback.
            error: Description of the original error.
            fallback: Description of the fallback action taken.
            context: Optional extra key/value pairs to include in the message.
            cooldown_seconds: Minimum seconds between duplicate alerts.

        Returns:
            True if the alert was dispatched, False if suppressed or failed.
        """
        now = datetime.now(tz=UTC)
        logger.warning(
            "fallback_activated",
            event_type="fallback_activated",
            component=component,
            error=error,
            fallback=fallback,
        )

        # Dedup: suppress if an identical alert was sent recently.
        dedup_key = (component, error[:50])
        last_sent = self._fallback_alert_history.get(dedup_key)
        if last_sent is not None:
            elapsed = (now - last_sent).total_seconds()
            if elapsed < cooldown_seconds:
                return False

        # Recursion guard: if we're already inside an alert send, bail out.
        if self._alerting:
            return False

        self._alerting = True
        try:
            lines = [
                f"**Fallback activated** in `{component}`",
                f"**Error:** {error}",
                f"**Fallback:** {fallback}",
            ]
            if context:
                for key, value in context.items():
                    lines.append(f"{key}: {value}")
            message = "\n".join(lines)

            await self._bot.send_message(CHANNEL_DEBUG, message)
            self._fallback_alert_history[dedup_key] = now
            return True
        except Exception:
            logger.exception(
                "fallback_alert_send_failed",
                component=component,
            )
            return False
        finally:
            self._alerting = False

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

        now = datetime.now(tz=UTC)

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

        now = datetime.now(tz=UTC)

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
