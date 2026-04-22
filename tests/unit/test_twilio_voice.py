"""Tests for Twilio Voice TTS integration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from donna.integrations.twilio_voice import TwilioVoice, _escape_xml


class TestTwilioVoice:
    def test_rate_limiting(self) -> None:
        voice = TwilioVoice(
            account_sid="test",
            auth_token="test",
            from_number="+1234567890",
            max_per_day=1,
        )
        assert voice.can_call() is True
        voice._calls_today.append(datetime.now(tz=UTC))
        assert voice.can_call() is False

    async def test_call_when_rate_limited(self) -> None:
        voice = TwilioVoice(
            account_sid="test",
            auth_token="test",
            from_number="+1234567890",
            max_per_day=0,
        )
        result = await voice.call("+0987654321", "Test message")
        assert result is False

    async def test_call_when_not_configured(self) -> None:
        voice = TwilioVoice(
            account_sid="",
            auth_token="",
            from_number="",
        )
        result = await voice.call("+0987654321", "Test message")
        assert result is False

    async def test_successful_call(self) -> None:
        voice = TwilioVoice(
            account_sid="test_sid",
            auth_token="test_token",
            from_number="+1234567890",
            max_per_day=5,
        )

        mock_call = MagicMock()
        mock_call.sid = "CA123"
        mock_call.status = "queued"

        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("twilio.rest.Client", return_value=mock_client):
            result = await voice.call("+0987654321", "Urgent task")

        assert result is True
        assert len(voice._calls_today) == 1
        mock_client.calls.create.assert_called_once()

        # Verify TwiML contains the message
        call_kwargs = mock_client.calls.create.call_args
        assert "Urgent task" in call_kwargs.kwargs["twiml"]


class TestEscapeXml:
    def test_escapes_ampersand(self) -> None:
        assert _escape_xml("A & B") == "A &amp; B"

    def test_escapes_angle_brackets(self) -> None:
        assert _escape_xml("<tag>") == "&lt;tag&gt;"

    def test_escapes_quotes(self) -> None:
        assert _escape_xml('He said "hello"') == "He said &quot;hello&quot;"

    def test_plain_text_unchanged(self) -> None:
        assert _escape_xml("Hello world") == "Hello world"
