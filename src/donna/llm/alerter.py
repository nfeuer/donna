"""Debounced Discord alerting for LLM gateway events."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger()

# Notifier type matches BudgetGuard: async callable(channel_name, message)
Notifier = Callable[[str, str], Awaitable[None]]


class GatewayAlerter:
    """Sends debounced alerts to Discord for gateway events.

    Each (alert_type, caller) pair is debounced independently.
    """

    def __init__(
        self,
        notifier: Notifier,
        debounce_minutes: int = 10,
    ) -> None:
        self._notifier = notifier
        self._debounce_seconds = debounce_minutes * 60
        self._last_sent: dict[str, float] = {}

    async def _send(self, key: str, message: str) -> None:
        """Send alert if not debounced."""
        now = time.monotonic()
        last = self._last_sent.get(key)
        if last is not None and now - last < self._debounce_seconds:
            return
        self._last_sent[key] = now
        try:
            await self._notifier("debug", message)
        except Exception:
            logger.exception("gateway_alert_failed", key=key)

    async def alert_rate_limited(
        self, caller: str, current_rpm: int, limit_rpm: int
    ) -> None:
        key = f"rate_limit:{caller}"
        msg = (
            f"LLM Gateway: **{caller}** is being rate-limited — "
            f"{current_rpm} req/min (limit: {limit_rpm})"
        )
        await self._send(key, msg)

    async def alert_queue_depth(
        self, current_depth: int, warning_threshold: int
    ) -> None:
        key = "queue_depth"
        msg = (
            f"LLM Gateway: backlog — "
            f"{current_depth} external requests queued "
            f"(warning at {warning_threshold})"
        )
        await self._send(key, msg)

    async def alert_queue_full(self, caller: str, max_depth: int) -> None:
        key = f"queue_full:{caller}"
        msg = (
            f"LLM Gateway: full — rejecting requests from **{caller}** "
            f"(queue: {max_depth}/{max_depth})"
        )
        await self._send(key, msg)

    async def alert_starvation(self, caller: str, interrupt_count: int) -> None:
        key = f"starvation:{caller}"
        msg = (
            f"LLM Gateway: external request from **{caller}** interrupted "
            f"{interrupt_count}x — promoting to prevent starvation"
        )
        await self._send(key, msg)

    async def alert_budget(
        self, spent: float, limit: float, pct: int
    ) -> None:
        key = "external_budget"
        msg = (
            f"LLM Gateway: external spend at {pct}% of daily limit "
            f"(${spent:.2f}/${limit:.2f})"
        )
        await self._send(key, msg)

    def update_debounce(self, debounce_minutes: int) -> None:
        """Hot-reload debounce interval."""
        self._debounce_seconds = debounce_minutes * 60
