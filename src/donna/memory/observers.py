"""Module-level observer registry for the memory ingest pipeline.

Slice 14 chose a hybrid wiring (brief §6):

- :class:`donna.tasks.database.Database` accepts a ``memory_observer``
  in its constructor (Option A) — the DB already takes a handful of
  collaborators, so one more pointer is fine and the call sites stay
  exact.
- :mod:`donna.preferences.correction_logger` is a loose module-level
  function. Threading an observer through every ``log_correction``
  caller would widen the public surface and churn unrelated call
  sites. Instead, the logger looks up a registered callback here and
  fires it best-effort — exactly what
  :func:`register_observer` / :func:`dispatch` do.

All callbacks must be coroutine functions; failures are swallowed and
logged (:func:`structlog.warning` with ``memory_ingest_failed``), never
propagated to the caller. This matches the source modules'
fire-and-forget contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger()


Observer = Callable[[dict[str, Any]], Awaitable[None]]


_REGISTRY: dict[str, list[Observer]] = {}


def register_observer(source_type: str, callback: Observer) -> None:
    """Register ``callback`` to fire on every ``source_type`` event."""
    _REGISTRY.setdefault(source_type, []).append(callback)


def unregister_all(source_type: str | None = None) -> None:
    """Drop registered observers (for test isolation)."""
    if source_type is None:
        _REGISTRY.clear()
        return
    _REGISTRY.pop(source_type, None)


async def dispatch(source_type: str, event: dict[str, Any]) -> None:
    """Dispatch ``event`` to every registered observer.

    Awaited by the caller but exceptions per observer are swallowed
    (logged as ``memory_ingest_failed``). The source-of-truth write
    has already committed by the time dispatch runs, and failure in
    the memory pipeline must never unwind the caller.
    """
    callbacks = _REGISTRY.get(source_type) or []
    if not callbacks:
        return
    for cb in callbacks:
        try:
            await cb(event)
        except Exception as exc:
            logger.warning(
                "memory_ingest_failed",
                source_type=source_type,
                reason=str(exc),
            )


__all__ = [
    "Observer",
    "dispatch",
    "register_observer",
    "unregister_all",
]
