"""Unit test: gmail_search accepts page_token + returns next_page_token."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from donna.skills.tools.gmail_search import gmail_search


class _FakeClient:
    def __init__(self):
        self.last_page_token = None
        self._next = None

    async def search_emails(self, query: str, max_results: int, page_token: str | None = None):
        self.last_page_token = page_token
        # First call returns a next_page_token; subsequent call returns None
        self._next = "next-tok" if page_token is None else None
        return [
            SimpleNamespace(
                id="m1", sender="s", subject="t", snippet="sn",
                date=datetime(2026, 4, 19, tzinfo=UTC),
            )
        ]

    def get_last_next_page_token(self) -> str | None:
        return self._next


@pytest.mark.asyncio
async def test_page_token_passed_through() -> None:
    client = _FakeClient()
    result = await gmail_search(
        client=client, query="from:x@y.com", page_token="abc",
    )
    assert client.last_page_token == "abc"
    assert result["ok"] is True
    assert "next_page_token" in result


@pytest.mark.asyncio
async def test_next_page_token_returned() -> None:
    client = _FakeClient()
    result = await gmail_search(client=client, query="from:x@y.com")
    assert result["next_page_token"] == "next-tok"


@pytest.mark.asyncio
async def test_next_page_token_none_when_exhausted() -> None:
    client = _FakeClient()
    result = await gmail_search(
        client=client, query="from:x@y.com", page_token="some-tok",
    )
    assert result["next_page_token"] is None
