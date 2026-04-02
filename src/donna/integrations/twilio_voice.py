"""Twilio Voice integration for Tier 4 TTS phone escalation.

Places outbound TTS phone calls for urgent escalations (priority 5
or budget emergencies). Rate-limited to 1 call per day.

See docs/notifications.md — Escalation Tier 4.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import structlog

logger = structlog.get_logger()


class TwilioVoice:
    """Outbound TTS phone calls via Twilio Voice API.

    Safety constraints:
    - Rate limited to max_per_day calls (default 1)
    - Only used for priority 5 tasks or budget emergencies
    - Disabled by default (tier4_enabled must be True)
    """

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
        max_per_day: int = 1,
    ) -> None:
        self._account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID", "")
        self._auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN", "")
        self._from_number = from_number or os.environ.get("TWILIO_PHONE_NUMBER", "")
        self._max_per_day = max_per_day
        self._calls_today: list[datetime] = []

    def _prune_old_calls(self) -> None:
        """Remove call records older than 24 hours."""
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)
        self._calls_today = [dt for dt in self._calls_today if dt > cutoff]

    def can_call(self) -> bool:
        """Check if we're under the daily rate limit."""
        self._prune_old_calls()
        return len(self._calls_today) < self._max_per_day

    async def call(self, to: str, message: str) -> bool:
        """Place a TTS phone call.

        Args:
            to: Destination phone number (E.164 format).
            message: Text to speak via TTS.

        Returns:
            True if the call was initiated, False if rate-limited or failed.
        """
        if not self.can_call():
            logger.warning(
                "twilio_voice_rate_limited",
                max_per_day=self._max_per_day,
                calls_today=len(self._calls_today),
            )
            return False

        if not all([self._account_sid, self._auth_token, self._from_number]):
            logger.warning("twilio_voice_not_configured")
            return False

        try:
            from twilio.rest import Client

            client = Client(self._account_sid, self._auth_token)

            # Build TwiML for TTS
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                f'<Say voice="Polly.Amy">{_escape_xml(message)}</Say>'
                '<Pause length="1"/>'
                '<Say voice="Polly.Amy">Press any key to acknowledge.</Say>'
                '<Gather numDigits="1" timeout="10"/>'
                "</Response>"
            )

            call = client.calls.create(
                to=to,
                from_=self._from_number,
                twiml=twiml,
            )

            self._calls_today.append(datetime.now(tz=UTC))

            logger.info(
                "twilio_voice_call_placed",
                call_sid=call.sid,
                to=to,
                status=call.status,
            )
            return True

        except Exception:
            logger.exception("twilio_voice_call_failed", to=to)
            return False


def _escape_xml(text: str) -> str:
    """Escape XML special characters for TwiML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
