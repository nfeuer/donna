"""Structural type for the bot interface used by NotificationService.

NotificationService._send calls three methods on the bot. Exposing this
as a typing.Protocol lets test doubles satisfy the contract without
subclassing DonnaBot (which needs a live discord.py Client).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotProtocol(Protocol):
    async def send_message(self, channel_name: str, text: str) -> Any: ...
    async def send_embed(self, channel_name: str, embed: Any) -> Any: ...
    async def send_to_thread(self, thread_id: int, text: str) -> None: ...
