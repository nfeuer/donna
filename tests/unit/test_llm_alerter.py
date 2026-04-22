"""Tests for gateway alerter with debouncing."""
from __future__ import annotations

from unittest.mock import AsyncMock

from donna.llm.alerter import GatewayAlerter


class TestGatewayAlerter:
    async def test_sends_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        notifier.assert_called_once()
        call_args = notifier.call_args
        assert "test-caller" in call_args[0][1]

    async def test_debounces_same_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        assert notifier.call_count == 1  # debounced

    async def test_different_callers_not_debounced(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("caller-a", current_rpm=15, limit_rpm=10)
        await alerter.alert_rate_limited("caller-b", current_rpm=15, limit_rpm=10)
        assert notifier.call_count == 2

    async def test_different_alert_types_not_debounced(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        await alerter.alert_queue_depth(current_depth=15, warning_threshold=10)
        assert notifier.call_count == 2

    async def test_queue_depth_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_queue_depth(current_depth=15, warning_threshold=10)
        assert "15" in notifier.call_args[0][1]

    async def test_starvation_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_starvation("test-caller", interrupt_count=3)
        assert "test-caller" in notifier.call_args[0][1]
        assert "3" in notifier.call_args[0][1]

    async def test_notifier_failure_does_not_raise(self) -> None:
        notifier = AsyncMock(side_effect=Exception("Discord down"))
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        # Should not raise
        await alerter.alert_queue_depth(current_depth=15, warning_threshold=10)
