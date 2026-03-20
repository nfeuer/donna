"""Unit tests for NotificationService.

Verifies blackout/quiet-hour enforcement, queue flushing, and logging
without any real Discord connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from donna.config import TimeWindowConfig, TimeWindowsConfig
from donna.notifications.service import (
    CHANNEL_TASKS,
    NOTIF_REMINDER,
    NotificationService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_time_windows(
    blackout_start: int = 0,
    blackout_end: int = 6,
    quiet_start: int = 20,
    quiet_end: int = 24,
) -> TimeWindowsConfig:
    return TimeWindowsConfig(
        blackout=TimeWindowConfig(start_hour=blackout_start, end_hour=blackout_end),
        quiet_hours=TimeWindowConfig(start_hour=quiet_start, end_hour=quiet_end),
        work=TimeWindowConfig(start_hour=8, end_hour=17, days=[0, 1, 2, 3, 4]),
        personal=TimeWindowConfig(start_hour=17, end_hour=20),
        weekend=TimeWindowConfig(start_hour=6, end_hour=20, days=[5, 6]),
    )


def _make_calendar_config(tw: TimeWindowsConfig) -> MagicMock:
    cfg = MagicMock()
    cfg.time_windows = tw
    return cfg


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    bot.send_embed = AsyncMock(return_value=None)
    bot.send_to_thread = AsyncMock()
    return bot


def _make_service(blackout_start: int = 0, blackout_end: int = 6) -> tuple[NotificationService, MagicMock]:
    tw = _make_time_windows(blackout_start=blackout_start, blackout_end=blackout_end)
    cfg = _make_calendar_config(tw)
    bot = _make_bot()
    service = NotificationService(bot=bot, calendar_config=cfg, user_id="u1")
    return service, bot


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 20, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Blackout tests
# ---------------------------------------------------------------------------


class TestBlackout:
    async def test_blackout_blocks_all_priorities(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(2)  # 2 AM — blackout
            result = await service.dispatch(NOTIF_REMINDER, "hello", CHANNEL_TASKS, priority=5)

        assert result is False
        bot.send_message.assert_not_called()

    async def test_blackout_queues_notification(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(3)  # 3 AM — blackout
            await service.dispatch(NOTIF_REMINDER, "hello", CHANNEL_TASKS, priority=5)

        assert len(service._queue) == 1

    async def test_outside_blackout_sends_immediately(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)  # 10 AM — normal hours
            result = await service.dispatch(NOTIF_REMINDER, "hello", CHANNEL_TASKS, priority=2)

        assert result is True
        bot.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Quiet hours tests
# ---------------------------------------------------------------------------


class TestQuietHours:
    async def test_quiet_hours_block_low_priority(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(21)  # 9 PM — quiet hours
            result = await service.dispatch(NOTIF_REMINDER, "hello", CHANNEL_TASKS, priority=2)

        assert result is False
        bot.send_message.assert_not_called()

    async def test_quiet_hours_allow_priority_5(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(21)  # 9 PM — quiet hours
            result = await service.dispatch(NOTIF_REMINDER, "urgent", CHANNEL_TASKS, priority=5)

        assert result is True
        bot.send_message.assert_called_once()

    async def test_not_quiet_hours_sends_low_priority(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(14)  # 2 PM — normal
            result = await service.dispatch(NOTIF_REMINDER, "low prio", CHANNEL_TASKS, priority=1)

        assert result is True
        bot.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Queue flushing tests
# ---------------------------------------------------------------------------


class TestFlushQueue:
    async def test_flush_queue_replays_notifications(self) -> None:
        service, bot = _make_service()

        # Queue two notifications during blackout.
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(3)
            await service.dispatch(NOTIF_REMINDER, "msg1", CHANNEL_TASKS)
            await service.dispatch(NOTIF_REMINDER, "msg2", CHANNEL_TASKS)

        assert len(service._queue) == 2
        bot.send_message.assert_not_called()

        # Flush — no time restrictions at flush time.
        count = await service.flush_queue()

        assert count == 2
        assert len(service._queue) == 0
        assert bot.send_message.call_count == 2

    async def test_flush_queue_clears_on_empty(self) -> None:
        service, bot = _make_service()
        count = await service.flush_queue()
        assert count == 0
        bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Embed / thread dispatch
# ---------------------------------------------------------------------------


class TestDispatchVariants:
    async def test_embed_dispatched_via_send_embed(self) -> None:
        service, bot = _make_service()
        embed = discord.Embed(title="Test")

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            await service.dispatch(
                "digest", "text", "digest", priority=5, embed=embed
            )

        bot.send_embed.assert_called_once()
        bot.send_message.assert_not_called()

    async def test_thread_id_routes_to_send_to_thread(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            await service.dispatch(
                "overdue", "nudge", "tasks", priority=3, thread_id=99999
            )

        bot.send_to_thread.assert_called_once_with(99999, "nudge")
        bot.send_message.assert_not_called()
