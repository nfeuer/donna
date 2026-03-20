"""Integration test for Gmail client with injected mock service.

Tests the full create_draft and read_email flow end-to-end using
a manually constructed mock that mimics the Gmail API chain.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from donna.config import EmailConfig
from donna.integrations.gmail import GmailClient


def _make_config() -> EmailConfig:
    return EmailConfig(
        send_enabled=False,
        monitor_alias="donna-tasks@example.com",
        user_email="nick@example.com",
    )


def _sync_call(fn):
    """Simulate asyncio.to_thread by calling fn() synchronously."""
    return fn()


def _build_full_mock_service(
    draft_id: str = "draft-integration-001",
    message_id: str = "msg-integration-001",
) -> MagicMock:
    """Build a mock Gmail service with pre-wired return values."""
    service = MagicMock()

    # drafts().create() returns draft with ID
    service.users().drafts().create().execute.return_value = {"id": draft_id}

    # messages().get() returns a full message payload
    body_text = "Integration test email body"
    encoded = base64.urlsafe_b64encode(body_text.encode()).decode()
    service.users().messages().get().execute.return_value = {
        "id": message_id,
        "snippet": "Integration test",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Integration Test"},
                {"name": "From", "value": "test@example.com"},
                {"name": "To", "value": "nick@example.com"},
                {"name": "Date", "value": "Thu, 20 Mar 2026 10:00:00 +0000"},
            ],
            "body": {"data": encoded},
        },
    }

    # messages().list() returns list with one stub
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": message_id}]
    }

    return service


class TestGmailMockIntegration:
    async def test_create_and_verify_draft(self) -> None:
        """create_draft returns the correct draft ID from the mocked API."""
        service = _build_full_mock_service(draft_id="draft-abc")
        client = GmailClient(config=_make_config(), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            draft_id = await client.create_draft(
                to="nick@example.com",
                subject="Integration Test Draft",
                body="This is a test draft.",
            )

        assert draft_id == "draft-abc"

    async def test_read_email_returns_parsed_message(self) -> None:
        """read_email fetches and parses a message from the mocked API."""
        service = _build_full_mock_service(message_id="msg-xyz")
        client = GmailClient(config=_make_config(), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            msg = await client.read_email("msg-xyz")

        assert msg.id == "msg-xyz"
        assert msg.subject == "Integration Test"
        assert msg.sender == "test@example.com"
        assert "Integration test email body" in msg.body_text

    async def test_search_then_read(self) -> None:
        """search_emails fetches stubs then reads full messages."""
        service = _build_full_mock_service(message_id="msg-search-001")
        client = GmailClient(config=_make_config(), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            messages = await client.search_emails("is:unread", max_results=10)

        assert len(messages) == 1
        assert messages[0].subject == "Integration Test"

    async def test_send_draft_raises_by_default(self) -> None:
        """send_draft raises RuntimeError when send_enabled=False (default)."""
        service = _build_full_mock_service()
        client = GmailClient(config=_make_config(), service=service)

        with pytest.raises(RuntimeError, match="Email send is disabled"):
            await client.send_draft("any-draft-id")
