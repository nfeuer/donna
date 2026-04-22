"""AutomationScheduler — asyncio poll loop that dispatches due automations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()


class AutomationScheduler:
    def __init__(
        self,
        *,
        repository: Any,
        dispatcher: Any,
        poll_interval_seconds: int,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._repo = repository
        self._dispatcher = dispatcher
        self._poll = poll_interval_seconds
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run_once(self) -> None:
        now = self._now_fn()
        try:
            due = await self._repo.list_due(now)
        except Exception:
            logger.exception("automation_scheduler_list_due_failed")
            return
        for row in due:
            try:
                await self._dispatcher.dispatch(row)
            except Exception:
                logger.exception(
                    "automation_scheduler_dispatch_failed",
                    automation_id=getattr(row, "id", None),
                )

    async def run_forever(self) -> None:
        while not self._stop:
            await self.run_once()
            if self._stop:
                return
            await self._sleep_fn(self._poll)
