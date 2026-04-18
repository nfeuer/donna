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


@pytest.mark.asyncio
async def test_dedup_pending_reply_bypasses_dispatcher():
    """A user replying 'merge' to an active dedup prompt must NOT hit the dispatcher."""
    from donna.tasks.database import TaskRow

    dispatcher = _FakeDispatcher()
    bot = _make_bot(dispatcher)

    # Seed a pending dedup entry for user u1.
    existing = TaskRow(
        id="existing-1",
        user_id="u1",
        title="existing task",
        description=None,
        domain="personal",
        priority=2,
        status="backlog",
        estimated_duration=None,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=None,
        parent_task=None,
        prep_work_flag=False,
        prep_work_instructions=None,
        agent_eligible=False,
        assigned_agent=None,
        agent_status=None,
        tags=None,
        notes=None,
        reschedule_count=0,
        created_at="2026-04-17T00:00:00",
        created_via="discord",
        estimated_cost=None,
        calendar_event_id=None,
        donna_managed=False,
        nudge_count=0,
        quality_score=None,
    )
    bot._dedup_pending["u1"] = ("new task title", None, "", existing)

    # Patch the legacy handler to track invocation without side effects.
    handler_called = {"flag": False}

    async def _fake_dedup_handler(message, user_id, log):
        handler_called["flag"] = True
        # Simulate handler's pop behavior.
        bot._dedup_pending.pop(user_id, None)

    bot._handle_dedup_reply = _fake_dedup_handler  # type: ignore[method-assign]

    msg = _make_message(content="merge", channel_id=100, author_id="u1")

    await bot.on_message(msg)

    assert handler_called["flag"] is True, "legacy dedup handler must run"
    assert dispatcher.received == [], "dispatcher must NOT receive message during dedup reply"


@pytest.mark.asyncio
async def test_edit_button_stores_pending_draft():
    """Clicking Edit on the confirmation card stores a PendingDraft in the
    dispatcher's registry so the next message resumes the edit flow.

    MVP behaviour (F-W3-F): the stored draft carries an ``edit_snapshot`` of
    the in-progress DraftAutomation, keyed on the DM-fallback key
    ``dm:{user_id}`` — DiscordIntentDispatcher._resume picks it up on the
    user's next message.
    """
    from donna.integrations.discord_pending_drafts import PendingDraftRegistry
    from donna.orchestrator.discord_intent_dispatcher import DraftAutomation

    # Build a fake dispatcher with a real PendingDraftRegistry so we can
    # inspect stored drafts.
    class _DispatcherStub:
        def __init__(self, drafts):
            self._drafts = drafts

    drafts = PendingDraftRegistry()
    bot = _make_bot(_DispatcherStub(drafts))

    draft = DraftAutomation(
        user_id="42",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 12 * * *",
        schedule_human="daily at noon",
        alert_conditions={"expression": "price_drop"},
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 */6 * * *",
    )

    class _FakeView:
        pass

    view = _FakeView()
    view.draft = draft
    view.name = "product_watch_shirt"
    view.result = "edit"

    msg = _make_message(content="create watch", channel_id=100, author_id=42)

    log = MagicMock()
    await bot._store_automation_edit_draft(msg, view, log)

    stored = drafts.get_by_thread("dm:42")
    assert stored is not None, "PendingDraft must be stored under dm:{user_id}"
    assert stored.draft_kind == "automation"
    assert stored.capability_name == "product_watch"
    snapshot = stored.partial.get("edit_snapshot")
    assert snapshot is not None
    assert snapshot["capability_name"] == "product_watch"
    assert snapshot["inputs"] == {"url": "https://x.com/shirt"}
    assert snapshot["target_cadence_cron"] == "0 12 * * *"


@pytest.mark.asyncio
async def test_field_update_bypasses_dispatcher():
    """A field-update message like 'change priority to 3' must NOT hit the dispatcher."""
    dispatcher = _FakeDispatcher()
    bot = _make_bot(dispatcher)

    handler_called = {"flag": False}

    async def _fake_field_update_handler(
        message, user_id, raw_text, field_update, log
    ):
        handler_called["flag"] = True

    bot._handle_field_update = _fake_field_update_handler  # type: ignore[method-assign]

    msg = _make_message(
        content="change priority to 3",
        channel_id=100,
        author_id="u1",
    )

    await bot.on_message(msg)

    assert handler_called["flag"] is True, "legacy field-update handler must run"
    assert dispatcher.received == [], "dispatcher must NOT receive field-update message"
