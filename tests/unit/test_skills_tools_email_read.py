"""Tests for email_read skill-system tool."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.tools.email_read import email_read, EmailReadError


class FakeEmailMessage:
    def __init__(
        self, *, id: str, sender: str, subject: str, snippet: str,
        date: datetime,
    ):
        self.id = id
        self.sender = sender
        self.subject = subject
        self.snippet = snippet
        self.date = date
        self.body_text = ""
        self.recipients = ["nick@example.com"]


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.search_emails = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_email_read_composes_full_query(fake_client):
    fake_client.search_emails.return_value = [
        FakeEmailMessage(
            id="m1", sender="Jane <jane@x.com>", subject="Re: Q2",
            snippet="Let me know", date=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    out = await email_read(
        client=fake_client,
        from_sender="jane@x.com",
        subject_contains="Q2",
        is_unread=True,
        since="2026-04-18",
        until="2026-04-21",
        max_results=5,
    )
    assert out["ok"] is True
    q = fake_client.search_emails.call_args.kwargs["query"]
    assert "from:jane@x.com" in q
    assert 'subject:"Q2"' in q
    assert "is:unread" in q
    assert "after:2026/04/18" in q
    assert "before:2026/04/21" in q
    assert out["messages"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_email_read_single_filter_is_enough(fake_client):
    fake_client.search_emails.return_value = []
    out = await email_read(client=fake_client, from_sender="boss@example.com")
    assert out["ok"] is True
    assert fake_client.search_emails.call_args.kwargs["query"] == "from:boss@example.com"


@pytest.mark.asyncio
async def test_email_read_requires_at_least_one_filter(fake_client):
    with pytest.raises(EmailReadError):
        await email_read(client=fake_client)


@pytest.mark.asyncio
async def test_email_read_is_unread_false_yields_read_filter(fake_client):
    fake_client.search_emails.return_value = []
    await email_read(client=fake_client, is_unread=False)
    assert "is:read" in fake_client.search_emails.call_args.kwargs["query"]


@pytest.mark.asyncio
async def test_email_read_clamps_max_results(fake_client):
    fake_client.search_emails.return_value = []
    await email_read(client=fake_client, from_sender="x@y", max_results=500)
    assert fake_client.search_emails.call_args.kwargs["max_results"] == 100


@pytest.mark.asyncio
async def test_email_read_bad_since_date_raises(fake_client):
    with pytest.raises(EmailReadError):
        await email_read(client=fake_client, since="21-04-2026")


@pytest.mark.asyncio
async def test_email_read_propagates_client_failure(fake_client):
    fake_client.search_emails.side_effect = RuntimeError("token expired")
    with pytest.raises(EmailReadError):
        await email_read(client=fake_client, from_sender="x@y")


@pytest.mark.asyncio
async def test_email_read_never_calls_write_methods(fake_client):
    fake_client.create_draft = AsyncMock()
    fake_client.send_draft = AsyncMock()
    fake_client.search_emails.return_value = []
    await email_read(client=fake_client, from_sender="x@y")
    fake_client.create_draft.assert_not_called()
    fake_client.send_draft.assert_not_called()
