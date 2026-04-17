"""DonnaBot.on_message routes through DiscordIntentDispatcher (Wave 3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from donna.integrations.discord_bot import DonnaBot


class _FakeDispatcher:
    def __init__(self):
        self.received: list[str] = []

    async def dispatch(self, msg):
        self.received.append(msg.content)
        from donna.orchestrator.discord_intent_dispatcher import DispatchResult

        return DispatchResult(kind="task_created", task_id="t1")


def _make_bot(intent_dispatcher):
    parser = MagicMock()
    db = MagicMock()
    with patch.object(discord.Client, "__init__", return_value=None):
        bot = DonnaBot(
            input_parser=parser,
            database=db,
            tasks_channel_id=100,
            intent_dispatcher=intent_dispatcher,
        )
    return bot


def _make_message(
    *,
    content: str,
    channel_id: int,
    author_id: int,
    author_is_bot: bool = False,
    thread=None,
):
    msg = MagicMock()
    msg.content = content
    msg.channel.id = channel_id
    msg.channel.send = AsyncMock()
    msg.author.bot = author_is_bot
    msg.author.id = author_id
    msg.thread = thread
    return msg


@pytest.mark.asyncio
async def test_on_message_in_tasks_channel_calls_intent_dispatcher():
    dispatcher = _FakeDispatcher()
    bot = _make_bot(dispatcher)
    msg = _make_message(
        content="watch https://x.com/shirt daily",
        channel_id=100,
        author_id=42,
    )

    await bot.on_message(msg)
    assert dispatcher.received == ["watch https://x.com/shirt daily"]


@pytest.mark.asyncio
async def test_on_message_bot_author_ignored():
    dispatcher = _FakeDispatcher()
    bot = _make_bot(dispatcher)
    msg = _make_message(
        content="hi",
        channel_id=100,
        author_id=42,
        author_is_bot=True,
    )
    await bot.on_message(msg)
    assert dispatcher.received == []


@pytest.mark.asyncio
async def test_on_message_other_channel_uses_legacy_flow():
    """When channel.id != tasks_channel_id, dispatcher must not fire."""
    dispatcher = _FakeDispatcher()
    bot = _make_bot(dispatcher)
    msg = _make_message(
        content="hi",
        channel_id=999,  # not the tasks channel
        author_id=42,
    )
    await bot.on_message(msg)
    assert dispatcher.received == []


@pytest.mark.asyncio
async def test_on_message_without_dispatcher_falls_back_to_legacy():
    """When intent_dispatcher=None, the legacy input_parser path runs."""
    parser = AsyncMock()
    # Simulate low-confidence parse so the legacy path exits cleanly
    # without touching DB/other mocks.
    from donna.orchestrator.input_parser import TaskParseResult

    parser.parse = AsyncMock(
        return_value=TaskParseResult(
            title="hi",
            description=None,
            domain="personal",
            priority=1,
            deadline=None,
            deadline_type="none",
            estimated_duration=15,
            recurrence=None,
            tags=None,
            prep_work_flag=False,
            agent_eligible=False,
            confidence=0.1,  # below threshold
        )
    )
    db = AsyncMock()
    with patch.object(discord.Client, "__init__", return_value=None):
        bot = DonnaBot(
            input_parser=parser,
            database=db,
            tasks_channel_id=100,
            intent_dispatcher=None,
        )
    msg = _make_message(
        content="hi",
        channel_id=100,
        author_id=42,
    )
    # Should not crash; legacy InputParser flow runs.
    await bot.on_message(msg)
    parser.parse.assert_called_once()


@pytest.mark.asyncio
async def test_on_message_clarification_posted_creates_thread():
    """kind=clarification_posted → bot tries to create a thread + send question."""
    from donna.orchestrator.discord_intent_dispatcher import DispatchResult

    class _Disp:
        async def dispatch(self, msg):
            return DispatchResult(
                kind="clarification_posted",
                clarifying_question="What URL?",
            )

    bot = _make_bot(_Disp())
    msg = _make_message(content="watch shirt", channel_id=100, author_id=42)
    thread = MagicMock()
    thread.send = AsyncMock()
    msg.create_thread = AsyncMock(return_value=thread)

    await bot.on_message(msg)
    msg.create_thread.assert_called_once()
    thread.send.assert_called_once_with("What URL?")


@pytest.mark.asyncio
async def test_on_message_task_created_sends_confirmation():
    """kind=task_created → bot sends a confirmation reply."""
    dispatcher = _FakeDispatcher()
    bot = _make_bot(dispatcher)
    msg = _make_message(content="buy milk", channel_id=100, author_id=42)

    await bot.on_message(msg)
    msg.channel.send.assert_called_once()
    sent = msg.channel.send.call_args[0][0]
    assert "t1" in sent
