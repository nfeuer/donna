"""Twilio SMS integration for Donna.

Outbound SMS: rate-limited to max 10/day (config), blocked during blackout hours.
Inbound SMS: webhook signature verification via RequestValidator.

Credentials sourced from environment variables only:
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_WEBHOOK_URL

Direct Python API pattern — no MCP wrapping.
See docs/integrations.md and slices/slice_07_sms_escalation.md.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import structlog

from donna.config import SmsConfig

logger = structlog.get_logger()


class TwilioSMS:
    """Async wrapper around twilio.rest.Client for outbound/inbound SMS.

    Usage:
        sms = TwilioSMS(config)
        sent = await sms.send(to="+15555555555", body="Hello!")
        valid = sms.verify_signature(url, params, signature)
    """

    def __init__(self, config: SmsConfig) -> None:
        self._config = config
        self._account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self._auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self._from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
        self._webhook_url = os.environ.get("TWILIO_WEBHOOK_URL", "")

        # In-memory daily rate limit counter.
        self._sent_today: int = 0
        self._counter_date: str = ""

    def _reset_counter_if_new_day(self, now: datetime) -> None:
        today = now.date().isoformat()
        if self._counter_date != today:
            self._sent_today = 0
            self._counter_date = today

    def _is_blackout(self, now: datetime) -> bool:
        hour = now.hour
        return self._config.blackout.start_hour <= hour < self._config.blackout.end_hour

    def _get_client(self):  # type: ignore[return]
        """Lazily import and return a Twilio REST client."""
        from twilio.rest import Client  # type: ignore[import-untyped]
        return Client(self._account_sid, self._auth_token)

    async def send(self, to: str, body: str) -> bool:
        """Send an outbound SMS via Twilio API.

        Enforces blackout hours and daily rate limit. Returns True if sent,
        False if blocked by rate limit or blackout.

        Args:
            to: Destination E.164 phone number (e.g. "+15555555555").
            body: SMS message body (max 1600 chars).
        """
        now = datetime.now(tz=timezone.utc)
        self._reset_counter_if_new_day(now)

        log = logger.bind(to=to, body_len=len(body))

        if self._is_blackout(now):
            log.info("sms_blocked_blackout", hour=now.hour)
            return False

        if self._sent_today >= self._config.rate_limit.max_per_day:
            log.warning(
                "sms_rate_limit_reached",
                sent_today=self._sent_today,
                max_per_day=self._config.rate_limit.max_per_day,
            )
            return False

        def _do_send() -> str:
            client = self._get_client()
            message = client.messages.create(
                body=body,
                from_=self._from_number,
                to=to,
            )
            return message.sid

        try:
            sid = await asyncio.to_thread(_do_send)
            self._sent_today += 1
            log.info("sms_sent", sid=sid, sent_today=self._sent_today)
            return True
        except Exception:
            log.exception("sms_send_failed")
            return False

    def verify_signature(
        self,
        url: str,
        params: dict[str, str],
        signature: str,
    ) -> bool:
        """Validate a Twilio webhook request signature.

        Args:
            url: Full URL of the webhook endpoint (must match Twilio config).
            params: POST form parameters from the request.
            signature: Value of X-Twilio-Signature header.

        Returns:
            True if signature is valid, False otherwise.
        """
        try:
            from twilio.request_validator import RequestValidator  # type: ignore[import-untyped]
            validator = RequestValidator(self._auth_token)
            result: bool = validator.validate(url, params, signature)
            return result
        except Exception:
            logger.exception("sms_signature_verification_failed")
            return False
