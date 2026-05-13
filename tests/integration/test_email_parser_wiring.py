"""Integration tests for email parser forwarded message detection."""

from __future__ import annotations

from donna.integrations.email_parser import ForwardedEmail, parse_forwarded


class TestParseForwarded:
    def test_detects_standard_forwarded_email(self) -> None:
        body = (
            "FYI see below.\n\n"
            "-------- Forwarded Message --------\n"
            "From: boss@example.com\n"
            "Sent: Thu, 20 Mar 2026 09:00:00 +0000\n"
            "To: donna@example.com\n"
            "Subject: Q1 Report\n\n"
            "Please get the Q1 report done by Friday."
        )
        result = parse_forwarded(body)
        assert result is not None
        assert isinstance(result, ForwardedEmail)
        assert result.original_sender == "boss@example.com"
        assert result.original_subject == "Q1 Report"
        assert "Q1 report" in result.original_body

    def test_returns_none_for_non_forwarded(self) -> None:
        body = "Hey, can you handle this task for me?"
        result = parse_forwarded(body)
        assert result is None

    def test_detects_begin_forwarded_message(self) -> None:
        body = (
            "Check this out:\n\n"
            "Begin forwarded message:\n\n"
            "From: alice@example.com\n"
            "Subject: Meeting notes\n\n"
            "Here are the meeting notes from today."
        )
        result = parse_forwarded(body)
        assert result is not None
        assert result.original_sender == "alice@example.com"
        assert result.original_subject == "Meeting notes"
