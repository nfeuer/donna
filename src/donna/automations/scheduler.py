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
        gpu_home_model: str | None = None,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._repo = repository
        self._dispatcher = dispatcher
        self._poll = poll_interval_seconds
        self._gpu_home_model = gpu_home_model
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._stop = False
        self._dispatching: set[str] = set()

    def stop(self) -> None:
        self._stop = True

    def _group_by_gpu_model(self, rows: list[Any]) -> list[Any]:
        """Reorder rows: home-model first, then each non-home group.

        Within each group, original order is preserved.
        """
        if not self._gpu_home_model:
            return rows

        home: list[Any] = []
        groups: dict[str, list[Any]] = {}

        for row in rows:
            gpu = getattr(row, "gpu_model", None)
            if gpu is None or gpu == self._gpu_home_model:
                home.append(row)
            else:
                groups.setdefault(gpu, []).append(row)

        result = list(home)
        for group in groups.values():
            result.extend(group)
        return result

    async def run_once(self) -> None:
        now = self._now_fn()
        try:
            due = await self._repo.list_due(now)
        except Exception:
            logger.exception("automation_scheduler_list_due_failed")
            return
        ordered = self._group_by_gpu_model(due)
        for row in ordered:
            aid: str | None = getattr(row, "id", None)
            if aid is None or aid in self._dispatching:
                continue
            self._dispatching.add(aid)
            try:
                await self._dispatcher.dispatch(row)
            except Exception:
                logger.exception(
                    "automation_scheduler_dispatch_failed",
                    automation_id=aid,
                )
            finally:
                self._dispatching.discard(aid)

    async def run_forever(self) -> None:
        while not self._stop:
            await self.run_once()
            if self._stop:
                return
            await self._sleep_fn(self._poll)
