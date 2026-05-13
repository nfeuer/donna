"""Tests for DM delivery via NotificationService.dispatch_dm."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from donna.notifications.service import (
    NOTIF_AUTOMATION_ALERT,
    NotificationService,
)


def _make_calendar_config():
    """Build a minimal CalendarConfig mock with time windows."""
    config = MagicMock()
    config.timezone = "UTC"
    config.time_windows.blackout.start_hour = 0
    config.time_windows.blackout.end_hour = 6
    config.time_windows.quiet_hours.start_hour = 20
    config.time_windows.quiet_hours.end_hour = 24
    return config


def _make_service(bot: AsyncMock | None = None) -> NotificationService:
    bot = bot or AsyncMock()
    return NotificationService(
        bot=bot,
        calendar_config=_make_calendar_config(),
        user_id="nick",
    )


class TestDispatchDm:
    async def test_sends_dm_during_active_hours(self):
        bot = AsyncMock()
        service = _make_service(bot)

        service._is_blackout = lambda now: False
        service._is_quiet = lambda now: False

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Price dropped below $50!",
            priority=5,
        )

        assert result is True
        bot.send_dm.assert_called_once_with("123456789", "Price dropped below $50!")

    async def test_queues_dm_during_blackout(self):
        bot = AsyncMock()
        service = _make_service(bot)

        service._is_blackout = lambda now: True

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Price dropped!",
            priority=2,
        )

        assert result is False
        bot.send_dm.assert_not_called()

    async def test_priority_5_passes_through_quiet_hours(self):
        bot = AsyncMock()
        service = _make_service(bot)

        service._is_blackout = lambda now: False
        service._is_quiet = lambda now: True

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Urgent alert!",
            priority=5,
        )

        assert result is True
        bot.send_dm.assert_called_once()

    async def test_low_priority_queued_during_quiet_hours(self):
        bot = AsyncMock()
        service = _make_service(bot)

        service._is_quiet = lambda now: True

        result = await service.dispatch_dm(
            discord_id="123456789",
            notification_type=NOTIF_AUTOMATION_ALERT,
            content="Non-urgent",
            priority=2,
        )

        assert result is False
        bot.send_dm.assert_not_called()
