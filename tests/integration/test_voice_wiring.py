"""Integration tests for TwilioVoice rate limiting and configuration."""

from __future__ import annotations

from datetime import UTC, datetime

from donna.integrations.twilio_voice import TwilioVoice


class TestTwilioVoiceRateLimit:
    def test_can_call_initially(self) -> None:
        voice = TwilioVoice(
            account_sid="test",
            auth_token="test",
            from_number="+15555555555",
            max_per_day=1,
        )
        assert voice.can_call() is True

    def test_cannot_call_after_max(self) -> None:
        voice = TwilioVoice(
            account_sid="test",
            auth_token="test",
            from_number="+15555555555",
            max_per_day=1,
        )
        voice._calls_today.append(datetime.now(tz=UTC))
        assert voice.can_call() is False

    async def test_call_rate_limited_returns_false(self) -> None:
        voice = TwilioVoice(
            account_sid="test",
            auth_token="test",
            from_number="+15555555555",
            max_per_day=0,
        )
        result = await voice.call(to="+15555555556", message="Test")
        assert result is False


class TestVoiceNotConfigured:
    async def test_call_returns_false_when_not_configured(self) -> None:
        voice = TwilioVoice()
        result = await voice.call(to="+15555555556", message="Test")
        assert result is False
