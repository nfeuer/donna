"""Tests for gmail_search + gmail_get_message skill-system tools."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.tools.gmail_get_message import gmail_get_message
from donna.skills.tools.gmail_search import GmailToolError, gmail_search


class FakeEmailMessage:
    def __init__(
        self, *, id: str, sender: str, subject: str, snippet: str,
        date: datetime, body: str = "", body_html: str | None = None,
    ):
        self.id = id
        self.sender = sender
        self.subject = subject
        self.snippet = snippet
        self.date = date
        self.recipients = ["nick@example.com"]
        self.body_text = body
        self.body_html = body_html


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.search_emails = AsyncMock()
    c.get_message = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_gmail_search_returns_summaries(fake_client):
    fake_client.search_emails.return_value = [
        FakeEmailMessage(
            id="m1", sender="Jane <jane@x.com>", subject="Re: Q2",
            snippet="Let me know...", date=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
        ),
    ]
    out = await gmail_search(
        client=fake_client, query="from:jane@x.com", max_results=5,
    )
    assert out["ok"] is True
    assert len(out["messages"]) == 1
    m = out["messages"][0]
    assert m["id"] == "m1"
    assert m["sender"].startswith("Jane")
    assert m["subject"] == "Re: Q2"
    assert m["snippet"].startswith("Let me know")
    assert m["internal_date"] == "2026-04-20T10:00:00+00:00"


@pytest.mark.asyncio
async def test_gmail_search_clamps_max_results(fake_client):
    fake_client.search_emails.return_value = []
    await gmail_search(client=fake_client, query="x", max_results=500)
    call_kwargs = fake_client.search_emails.call_args.kwargs
    assert call_kwargs["max_results"] == 100


@pytest.mark.asyncio
async def test_gmail_search_empty_query_raises(fake_client):
    with pytest.raises(GmailToolError):
        await gmail_search(client=fake_client, query="")


@pytest.mark.asyncio
async def test_gmail_search_propagates_client_failure(fake_client):
    fake_client.search_emails.side_effect = RuntimeError("token expired")
    with pytest.raises(GmailToolError):
        await gmail_search(client=fake_client, query="x")


@pytest.mark.asyncio
async def test_gmail_get_message_returns_body(fake_client):
    fake_client.get_message.return_value = FakeEmailMessage(
        id="m1", sender="Jane <jane@x.com>", subject="Re: Q2",
        snippet="Let me know...",
        date=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
        body="Hey — need your roadmap thoughts by Friday.",
    )
    out = await gmail_get_message(client=fake_client, message_id="m1")
    assert out["ok"] is True
    assert out["sender"] == "Jane <jane@x.com>"
    assert out["subject"] == "Re: Q2"
    assert out["body_plain"].startswith("Hey")
    assert out["body_html"] is None


@pytest.mark.asyncio
async def test_gmail_get_message_returns_html_when_plain_absent(fake_client):
    fake_client.get_message.return_value = FakeEmailMessage(
        id="m1", sender="Jane <jane@x.com>", subject="Newsletter",
        snippet="html-only email",
        date=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
        body="",
        body_html="<p>HTML body content</p>",
    )
    out = await gmail_get_message(client=fake_client, message_id="m1")
    assert out["ok"] is True
    assert out["body_plain"] == ""
    assert out["body_html"] == "<p>HTML body content</p>"


@pytest.mark.asyncio
async def test_gmail_tools_never_call_compose_or_send(fake_client):
    # Structural assertion: the wrappers must not reference these methods.
    fake_client.create_draft = AsyncMock()
    fake_client.send_draft = AsyncMock()
    fake_client.search_emails.return_value = []
    fake_client.get_message.return_value = FakeEmailMessage(
        id="m1", sender="x@y", subject="s", snippet="sn",
        date=datetime(2026, 4, 20, tzinfo=UTC),
    )
    await gmail_search(client=fake_client, query="x")
    await gmail_get_message(client=fake_client, message_id="m1")
    fake_client.create_draft.assert_not_called()
    fake_client.send_draft.assert_not_called()
