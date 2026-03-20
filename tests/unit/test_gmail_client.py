"""Unit tests for the Gmail client.

All Google API calls are mocked via an injected service object,
so no network or OAuth2 is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.config import EmailConfig
from donna.integrations.gmail import GmailClient, _extract_body_text, _parse_date, _parse_message


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_config(send_enabled: bool = False) -> EmailConfig:
    return EmailConfig(
        send_enabled=send_enabled,
        monitor_alias="donna-tasks@example.com",
        user_email="nick@example.com",
    )


def _make_service() -> MagicMock:
    """Build a mock Gmail API service object."""
    service = MagicMock()
    return service


def _make_message_payload(
    msg_id: str = "abc123",
    subject: str = "Test Subject",
    sender: str = "boss@example.com",
    to: str = "nick@example.com",
    date: str = "Thu, 20 Mar 2026 09:00:00 +0000",
    body: str = "Hello Nick",
) -> dict:
    """Build a fake Gmail API message dict."""
    import base64

    body_encoded = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "snippet": body[:50],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Date", "value": date},
            ],
            "body": {"data": body_encoded},
        },
    }


# ------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------


class TestAuthenticate:
    async def test_authenticate_skipped_when_service_injected(self) -> None:
        """Service injection means no OAuth2 flow is triggered."""
        mock_service = _make_service()
        client = GmailClient(config=_make_config(), service=mock_service)

        # authenticate() should return immediately without calling build()
        with patch("donna.integrations.gmail.asyncio.to_thread") as mock_thread:
            await client.authenticate()
            mock_thread.assert_not_called()

        assert client._service is mock_service

    async def test_svc_property_raises_when_not_authenticated(self) -> None:
        """Accessing _svc without authenticating raises RuntimeError."""
        client = GmailClient(config=_make_config())
        with pytest.raises(RuntimeError, match="Not authenticated"):
            _ = client._svc


# ------------------------------------------------------------------
# search_emails
# ------------------------------------------------------------------


class TestSearchEmails:
    async def test_search_emails_returns_list(self) -> None:
        """search_emails returns one EmailMessage per result."""
        service = _make_service()
        payload = _make_message_payload()

        # messages().list() returns stub with ID
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "abc123"}]
        }
        # messages().get() returns the full payload
        service.users().messages().get().execute.return_value = payload

        client = GmailClient(config=_make_config(), service=service)

        # Patch asyncio.to_thread to call the function synchronously.
        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            results = await client.search_emails("is:unread", max_results=5)

        assert len(results) == 1
        assert results[0].id == "abc123"
        assert results[0].subject == "Test Subject"

    async def test_search_emails_empty_inbox(self) -> None:
        """Returns empty list when no messages match."""
        service = _make_service()
        service.users().messages().list().execute.return_value = {"messages": []}

        client = GmailClient(config=_make_config(), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            results = await client.search_emails("from:nobody")

        assert results == []


# ------------------------------------------------------------------
# create_draft
# ------------------------------------------------------------------


class TestCreateDraft:
    async def test_create_draft_calls_api(self) -> None:
        """create_draft calls drafts().create() with correct body."""
        service = _make_service()
        service.users().drafts().create().execute.return_value = {"id": "draft-001"}

        client = GmailClient(config=_make_config(), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            draft_id = await client.create_draft(
                to="nick@example.com",
                subject="Hello",
                body="Test body",
            )

        assert draft_id == "draft-001"
        # Verify create() was called with the correct userId argument.
        service.users().drafts().create.assert_called_with(
            userId="me", body=service.users().drafts().create.call_args[1]["body"]
        )

    async def test_create_draft_returns_draft_id(self) -> None:
        """create_draft returns the draft ID string."""
        service = _make_service()
        service.users().drafts().create().execute.return_value = {"id": "xyz-draft"}

        client = GmailClient(config=_make_config(), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            result = await client.create_draft("a@b.com", "subj", "body")

        assert result == "xyz-draft"


# ------------------------------------------------------------------
# send_draft
# ------------------------------------------------------------------


class TestSendDraft:
    async def test_send_draft_raises_when_disabled(self) -> None:
        """send_draft raises RuntimeError when send_enabled=False."""
        client = GmailClient(config=_make_config(send_enabled=False), service=_make_service())
        with pytest.raises(RuntimeError, match="Email send is disabled"):
            await client.send_draft("draft-001")

    async def test_send_draft_succeeds_when_enabled(self) -> None:
        """send_draft calls drafts().send() when send_enabled=True."""
        service = _make_service()
        service.users().drafts().send().execute.return_value = {"id": "msg-001"}

        client = GmailClient(config=_make_config(send_enabled=True), service=service)

        with patch(
            "donna.integrations.gmail.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: _sync_call(fn),
        ):
            result = await client.send_draft("draft-001")

        assert result is True


# ------------------------------------------------------------------
# Helper tests
# ------------------------------------------------------------------


class TestParseMessage:
    def test_parse_message_extracts_headers(self) -> None:
        payload = _make_message_payload(
            subject="My Task",
            sender="boss@example.com",
            to="nick@example.com",
        )
        msg = _parse_message(payload)
        assert msg.subject == "My Task"
        assert msg.sender == "boss@example.com"
        assert "nick@example.com" in msg.recipients

    def test_parse_message_decodes_body(self) -> None:
        payload = _make_message_payload(body="Do the thing ASAP")
        msg = _parse_message(payload)
        assert "Do the thing ASAP" in msg.body_text


class TestExtractBodyText:
    def test_extracts_plain_text(self) -> None:
        import base64
        data = base64.urlsafe_b64encode(b"Hello world").decode()
        payload = {"mimeType": "text/plain", "body": {"data": data}}
        assert _extract_body_text(payload) == "Hello world"

    def test_returns_empty_for_unknown_mime(self) -> None:
        payload = {"mimeType": "text/html", "parts": []}
        assert _extract_body_text(payload) == ""

    def test_recurses_into_multipart(self) -> None:
        import base64
        data = base64.urlsafe_b64encode(b"Plain text").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": data}},
            ],
        }
        assert _extract_body_text(payload) == "Plain text"


class TestParseDate:
    def test_parses_rfc2822(self) -> None:
        dt = _parse_date("Thu, 20 Mar 2026 09:00:00 +0000")
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 20

    def test_returns_min_for_empty(self) -> None:
        from datetime import datetime, timezone
        result = _parse_date("")
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_returns_min_for_garbage(self) -> None:
        from datetime import datetime, timezone
        result = _parse_date("not-a-date")
        assert result == datetime.min.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _sync_call(fn):
    """Call a zero-argument callable synchronously (simulates to_thread)."""
    return fn()
