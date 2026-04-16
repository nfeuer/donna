"""Unit tests for magic-link email sender (via Gmail integration)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.api.auth import email_sender


@pytest.mark.asyncio
async def test_send_magic_link_creates_and_sends_draft():
    gmail = AsyncMock()
    gmail.create_draft.return_value = "draft123"
    gmail.send_draft.return_value = True

    await email_sender.send_magic_link(
        gmail,
        to="nick@example.com",
        token="opaque-token",
        verify_base_url="https://donna.houseoffeuer.com/auth/verify",
        from_name="Donna",
    )
    gmail.create_draft.assert_awaited_once()
    gmail.send_draft.assert_awaited_once_with("draft123")
    kwargs = gmail.create_draft.await_args.kwargs
    assert kwargs["to"] == "nick@example.com"
    assert "https://donna.houseoffeuer.com/auth/verify?token=opaque-token" in kwargs["body"]
    assert "Donna" in kwargs["subject"]


@pytest.mark.asyncio
async def test_send_magic_link_bubbles_send_failure():
    gmail = AsyncMock()
    gmail.create_draft.return_value = "draft123"
    gmail.send_draft.side_effect = RuntimeError("email send disabled")
    with pytest.raises(RuntimeError):
        await email_sender.send_magic_link(
            gmail,
            to="nick@example.com",
            token="opaque",
            verify_base_url="https://donna.houseoffeuer.com/auth/verify",
            from_name="Donna",
        )
