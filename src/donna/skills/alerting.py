"""Shared fallback-alert helper for the skill system.

CLAUDE.md requires every fallback / degraded-behaviour path to call
``dispatch_fallback_alert`` (or log with ``event_type="fallback_activated"``
when no notifier is wired). The skill-system components historically had **zero**
such calls (Fable critique #7) — run-persistence failures, shadow-sample loss,
and trust demotions were all silent. This module gives those components one
narrow, safe seam to alert through.

``FallbackAlert`` is a thin async callable type matching
:meth:`donna.notifications.service.NotificationService.dispatch_fallback_alert`.
:func:`emit_fallback_alert` always logs the ``fallback_activated`` event and,
when a notifier is wired, dispatches it — never raising into the caller.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()


class FallbackAlert(Protocol):
    """Async callable matching ``NotificationService.dispatch_fallback_alert``."""

    def __call__(
        self,
        component: str,
        error: str,
        fallback: str,
        context: dict[str, Any] | None = ...,
        cooldown_seconds: int = ...,
    ) -> Awaitable[bool]:
        ...


async def emit_fallback_alert(
    alert_fn: FallbackAlert | None,
    *,
    component: str,
    error: str,
    fallback: str,
    context: dict[str, Any] | None = None,
    cooldown_seconds: int = 3600,
) -> None:
    """Emit a fallback alert, logging unconditionally and dispatching if wired.

    Always logs ``fallback_activated`` so the event is observable even when no
    notifier is available (CLAUDE.md fallback rule). When *alert_fn* is set, the
    alert is dispatched; any failure dispatching is caught and logged so this
    helper never raises into the calling fallback path.

    Args:
        alert_fn: The notifier's ``dispatch_fallback_alert`` (or ``None``).
        component: Name of the subsystem activating the fallback.
        error: Description of the original error.
        fallback: Description of the fallback/degraded action taken.
        context: Optional extra key/value pairs for the alert message.
        cooldown_seconds: Minimum seconds between duplicate alerts.

    Returns:
        None.
    """
    logger.warning(
        "fallback_activated",
        event_type="fallback_activated",
        component=component,
        error=error,
        fallback=fallback,
        **(context or {}),
    )
    if alert_fn is None:
        return
    try:
        await alert_fn(
            component=component,
            error=error,
            fallback=fallback,
            context=context,
            cooldown_seconds=cooldown_seconds,
        )
    except Exception:
        logger.exception(
            "fallback_alert_dispatch_failed",
            component=component,
        )
