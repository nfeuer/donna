"""Unit tests for NotificationService.

Verifies blackout/quiet-hour enforcement, queue flushing, and logging
without any real Discord connection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from donna.config import NotificationPolicyConfig, TimeWindowConfig, TimeWindowsConfig
from donna.notifications.service import (
    CHANNEL_DEBUG,
    CHANNEL_TASKS,
    NOTIF_AUTOMATION_ALERT,
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
    cfg.timezone = "UTC"
    return cfg


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    bot.send_embed = AsyncMock(return_value=None)
    bot.send_to_thread = AsyncMock()
    return bot


def _make_service(
    blackout_start: int = 0, blackout_end: int = 6,
) -> tuple[NotificationService, MagicMock]:
    tw = _make_time_windows(blackout_start=blackout_start, blackout_end=blackout_end)
    cfg = _make_calendar_config(tw)
    bot = _make_bot()
    service = NotificationService(bot=bot, calendar_config=cfg, user_id="u1")
    return service, bot


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 20, hour, minute, tzinfo=UTC)


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
        service, _bot = _make_service()

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


# ---------------------------------------------------------------------------
# Fallback alert tests
# ---------------------------------------------------------------------------


class TestFallbackAlert:
    async def test_dispatches_to_debug_channel(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            result = await service.dispatch_fallback_alert(
                component="llm_router",
                error="Claude API timeout",
                fallback="switched to local model",
            )

        assert result is True
        bot.send_message.assert_called_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == CHANNEL_DEBUG
        message = call_args[0][1]
        assert "llm_router" in message
        assert "Claude API timeout" in message
        assert "switched to local model" in message

    async def test_includes_context_in_message(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            await service.dispatch_fallback_alert(
                component="scheduler",
                error="cron missed",
                fallback="manual retry",
                context={"task_id": "abc123", "attempt": "3"},
            )

        message = bot.send_message.call_args[0][1]
        assert "task_id: abc123" in message
        assert "attempt: 3" in message

    async def test_dedup_within_cooldown(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            r1 = await service.dispatch_fallback_alert(
                component="llm_router", error="timeout", fallback="local",
            )
            mock_dt.now.return_value = _utc(10, 30)  # 30 min later, within 1h cooldown
            r2 = await service.dispatch_fallback_alert(
                component="llm_router", error="timeout", fallback="local",
            )

        assert r1 is True
        assert r2 is False
        assert bot.send_message.call_count == 1

    async def test_different_component_not_deduped(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            r1 = await service.dispatch_fallback_alert(
                component="llm_router", error="timeout", fallback="local",
            )
            r2 = await service.dispatch_fallback_alert(
                component="scheduler", error="timeout", fallback="local",
            )

        assert r1 is True
        assert r2 is True
        assert bot.send_message.call_count == 2

    async def test_cooldown_expires(self) -> None:
        service, bot = _make_service()

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            r1 = await service.dispatch_fallback_alert(
                component="llm_router", error="timeout", fallback="local",
                cooldown_seconds=1800,  # 30 min cooldown
            )
            mock_dt.now.return_value = _utc(11)  # 1h later, past 30m cooldown
            r2 = await service.dispatch_fallback_alert(
                component="llm_router", error="timeout", fallback="local",
                cooldown_seconds=1800,
            )

        assert r1 is True
        assert r2 is True
        assert bot.send_message.call_count == 2

    async def test_recursion_guard_on_send_failure(self) -> None:
        service, bot = _make_service()
        bot.send_message = AsyncMock(side_effect=RuntimeError("Discord down"))

        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            result = await service.dispatch_fallback_alert(
                component="notifier", error="send failed", fallback="logged only",
            )

        assert result is False
        # Verify _alerting flag was reset (no lingering recursion guard)
        assert service._alerting is False


# ---------------------------------------------------------------------------
# Per-type window policy tests
# ---------------------------------------------------------------------------


def _make_bot_with_dm() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    bot.send_embed = AsyncMock(return_value=None)
    bot.send_to_thread = AsyncMock()
    bot.send_dm = AsyncMock(return_value=None)
    return bot


def _make_service_with_policy(
    policy: NotificationPolicyConfig,
) -> tuple[NotificationService, MagicMock]:
    tw = _make_time_windows()
    cfg = _make_calendar_config(tw)
    bot = _make_bot_with_dm()
    service = NotificationService(
        bot=bot, calendar_config=cfg, user_id="u1",
        notification_policy=policy,
    )
    return service, bot


class TestPerTypePolicies:
    async def test_blackout_exempt_type_sends_during_blackout(self) -> None:
        policy = NotificationPolicyConfig(
            blackout_exempt=[NOTIF_AUTOMATION_ALERT], quiet_exempt=[]
        )
        service, bot = _make_service_with_policy(policy)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(3)  # 3 AM — blackout
            sent = await service.dispatch_dm(
                "123", NOTIF_AUTOMATION_ALERT, "deal!", priority=3
            )
        assert sent is True
        bot.send_dm.assert_awaited_once()

    async def test_non_exempt_type_queues_during_blackout(self) -> None:
        policy = NotificationPolicyConfig(
            blackout_exempt=[NOTIF_AUTOMATION_ALERT], quiet_exempt=[]
        )
        service, bot = _make_service_with_policy(policy)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(3)  # 3 AM — blackout
            sent = await service.dispatch_dm("123", "overdue", "nudge", priority=3)
        assert sent is False
        bot.send_dm.assert_not_awaited()

    async def test_quiet_exempt_type_sends_during_quiet_hours(self) -> None:
        policy = NotificationPolicyConfig(
            blackout_exempt=[], quiet_exempt=[NOTIF_AUTOMATION_ALERT]
        )
        service, bot = _make_service_with_policy(policy)
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(21)  # 9 PM — quiet hours
            sent = await service.dispatch_dm(
                "123", NOTIF_AUTOMATION_ALERT, "deal!", priority=3
            )
        assert sent is True
        bot.send_dm.assert_awaited_once()

    async def test_no_policy_keeps_legacy_gating(self) -> None:
        tw = _make_time_windows()
        cfg = _make_calendar_config(tw)
        bot = _make_bot_with_dm()
        service = NotificationService(bot=bot, calendar_config=cfg, user_id="u1")
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(3)
            sent = await service.dispatch_dm(
                "123", NOTIF_AUTOMATION_ALERT, "deal!", priority=3
            )
        assert sent is False
        bot.send_dm.assert_not_awaited()
