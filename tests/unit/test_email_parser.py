"""Unit tests for the email forwarding parser."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.integrations.email_parser import (
    ForwardedEmail,
    _build_task_text,
    parse_forwarded,
    poll_and_create_tasks,
)


# ------------------------------------------------------------------
# parse_forwarded
# ------------------------------------------------------------------


class TestParseForwarded:
    def test_parse_standard_forwarded_email(self) -> None:
        """Detects '--- Forwarded message ---' separator."""
        body = """\
-------- Forwarded Message --------
From: boss@example.com
Sent: Thu, 20 Mar 2026 09:00:00 +0000
To: donna-tasks@example.com
Subject: Q1 Report

Hi Nick, please get the Q1 report done by Friday.
"""
        result = parse_forwarded(body)
        assert result is not None
        assert result.original_sender == "boss@example.com"
        assert result.original_subject == "Q1 Report"
        assert "Q1 report done by Friday" in result.original_body

    def test_parse_begin_forwarded_message(self) -> None:
        """Detects 'Begin forwarded message' separator."""
        body = """\
Begin forwarded message

From: alice@example.com
Subject: Urgent task

Please review the contract.
"""
        result = parse_forwarded(body)
        assert result is not None
        assert result.original_sender == "alice@example.com"
        assert result.original_subject == "Urgent task"

    def test_parse_original_message_separator(self) -> None:
        """Detects '_____ Original Message _____' separator."""
        body = """\
_____ Original Message _____
From: bob@example.com
Subject: Call tomorrow

Can we schedule a call?
"""
        result = parse_forwarded(body)
        assert result is not None

    def test_returns_none_for_non_forwarded(self) -> None:
        """Returns None when no forwarded marker is present."""
        body = "Hey, can you buy milk?"
        assert parse_forwarded(body) is None

    def test_returns_none_for_empty_body(self) -> None:
        assert parse_forwarded("") is None

    def test_extracts_body_after_header_block(self) -> None:
        """Body text follows blank line after forwarded headers."""
        body = """\
-------- Forwarded Message --------
From: someone@example.com
Subject: Do the thing

This is the actual task content.
It spans multiple lines.
"""
        result = parse_forwarded(body)
        assert result is not None
        assert "actual task content" in result.original_body
        assert "multiple lines" in result.original_body


# ------------------------------------------------------------------
# _build_task_text
# ------------------------------------------------------------------


class TestBuildTaskText:
    def test_includes_subject_and_body(self) -> None:
        fw = ForwardedEmail(
            original_sender="boss@example.com",
            original_subject="Finish the report",
            original_body="The Q1 report needs to be done by Friday.",
            raw_body="",
        )
        text = _build_task_text(fw)
        assert "Finish the report" in text
        assert "Q1 report" in text
        assert "boss@example.com" in text

    def test_handles_missing_fields(self) -> None:
        fw = ForwardedEmail(
            original_sender="",
            original_subject="",
            original_body="Just do the thing",
            raw_body="",
        )
        text = _build_task_text(fw)
        assert "Just do the thing" in text


# ------------------------------------------------------------------
# poll_and_create_tasks
# ------------------------------------------------------------------


class TestPollAndCreateTasks:
    async def test_poll_creates_task_from_forwarded_email(self) -> None:
        """poll_and_create_tasks calls input_parser and creates a task."""
        # Build a mock EmailMessage with forwarded content.
        from donna.integrations.gmail import EmailMessage
        from datetime import datetime, timezone

        forwarded_body = """\
-------- Forwarded Message --------
From: boss@example.com
Subject: Update the roadmap

Please update the roadmap document.
"""
        mock_msg = EmailMessage(
            id="msg-001",
            subject="Fwd: Update the roadmap",
            sender="nick@example.com",
            recipients=["donna-tasks@example.com"],
            body_text=forwarded_body,
            snippet="forwarded",
            date=datetime.now(tz=timezone.utc),
        )

        mock_gmail = AsyncMock()
        mock_gmail.search_emails.return_value = [mock_msg]

        # Mock InputParser result.
        from donna.orchestrator.input_parser import TaskParseResult
        parse_result = TaskParseResult(
            title="Update the roadmap",
            description="Update the roadmap document.",
            domain="WORK",
            priority=2,
            deadline=None,
            deadline_type="none",
            estimated_duration=60,
            recurrence=None,
            tags=[],
            prep_work_flag=False,
            agent_eligible=False,
            confidence=0.9,
        )
        mock_parser = AsyncMock()
        mock_parser.parse.return_value = parse_result

        mock_db = AsyncMock()

        count = await poll_and_create_tasks(
            gmail=mock_gmail,
            input_parser=mock_parser,
            db=mock_db,
            user_id="nick",
            monitor_alias="donna-tasks@example.com",
        )

        assert count == 1
        mock_parser.parse.assert_called_once()
        mock_db.create_task.assert_called_once()

    async def test_poll_skips_non_forwarded_emails(self) -> None:
        """Messages without forwarded structure are skipped."""
        from donna.integrations.gmail import EmailMessage
        from datetime import datetime, timezone

        mock_msg = EmailMessage(
            id="msg-002",
            subject="Random email",
            sender="someone@example.com",
            recipients=["donna-tasks@example.com"],
            body_text="Hey Nick, how's it going?",  # Not forwarded.
            snippet="hey",
            date=datetime.now(tz=timezone.utc),
        )

        mock_gmail = AsyncMock()
        mock_gmail.search_emails.return_value = [mock_msg]
        mock_parser = AsyncMock()
        mock_db = AsyncMock()

        count = await poll_and_create_tasks(
            gmail=mock_gmail,
            input_parser=mock_parser,
            db=mock_db,
            user_id="nick",
            monitor_alias="donna-tasks@example.com",
        )

        assert count == 0
        mock_parser.parse.assert_not_called()

    async def test_poll_handles_search_failure_gracefully(self) -> None:
        """Returns 0 when Gmail search raises an exception."""
        mock_gmail = AsyncMock()
        mock_gmail.search_emails.side_effect = Exception("Network error")
        mock_parser = AsyncMock()
        mock_db = AsyncMock()

        count = await poll_and_create_tasks(
            gmail=mock_gmail,
            input_parser=mock_parser,
            db=mock_db,
            user_id="nick",
            monitor_alias="donna-tasks@example.com",
        )

        assert count == 0
