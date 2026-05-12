"""Lightweight async pub/sub for task lifecycle events.

Subscribers receive (task, **context) and must be async callables.
Exceptions in subscribers are logged and swallowed — a failing
subscriber must never break the caller's flow.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger()

Callback = Callable[..., Coroutine[Any, Any, None]]


class TaskEventBus:
    """In-process async event bus for task lifecycle events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callback) -> None:
        self._subscribers[event_type].append(callback)

    async def emit(self, event_type: str, *, task: Any, **context: Any) -> None:
        for callback in self._subscribers.get(event_type, []):
            try:
                await callback(task, **context)
            except Exception:
                logger.exception(
                    "event_subscriber_failed",
                    event_type=event_type,
                    subscriber=getattr(callback, "__qualname__", str(callback)),
                )
