"""Tests for the BotProtocol structural type."""

from __future__ import annotations

from donna.notifications.bot_protocol import BotProtocol


def test_real_donnabot_has_required_methods() -> None:
    from donna.integrations.discord_bot import DonnaBot
    for attr in ("send_message", "send_embed", "send_to_thread"):
        assert hasattr(DonnaBot, attr), f"DonnaBot missing {attr}"


def test_simple_fake_satisfies_protocol() -> None:
    class Fake:
        async def send_message(self, channel: str, content: str) -> None: ...
        async def send_embed(self, channel: str, embed) -> None: ...
        async def send_to_thread(self, thread_id: int, content: str) -> None: ...

    fake = Fake()
    assert isinstance(fake, BotProtocol)


def test_object_missing_method_fails_protocol() -> None:
    class Incomplete:
        async def send_message(self, channel: str, content: str) -> None: ...

    assert not isinstance(Incomplete(), BotProtocol)
