"""Donna aiohttp web server.

Exposes the health endpoint and serves as the process entrypoint for
the orchestrator container. Business logic lives elsewhere; this module
is pure infrastructure.

Notification background tasks (ReminderScheduler, OverdueDetector,
MorningDigest) are accepted via NotificationTasks and started as
asyncio tasks when run_server() is called.
"""

from __future__ import annotations

import asyncio
import dataclasses
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from aiohttp import web

if TYPE_CHECKING:
    from donna.notifications.digest import MorningDigest
    from donna.notifications.overdue import OverdueDetector
    from donna.notifications.reminders import ReminderScheduler


@dataclasses.dataclass
class NotificationTasks:
    """Container for Slice-5 background task runners."""

    reminder_scheduler: ReminderScheduler
    overdue_detector: OverdueDetector
    morning_digest: MorningDigest


async def health_handler(request: web.Request) -> web.Response:
    """GET /health — liveness probe used by Docker healthcheck."""
    return web.json_response(
        {
            "status": "healthy",
            "service": "donna-orchestrator",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )


def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()
    app.router.add_get("/health", health_handler)
    return app


async def run_server(
    host: str = "0.0.0.0",  # noqa: S104
    port: int = 8100,
    notification_tasks: NotificationTasks | None = None,
) -> None:
    """Start the aiohttp server and block until shutdown signal received.

    If notification_tasks is supplied, the three background loops are
    started as asyncio tasks and cancelled cleanly on shutdown.
    """
    logger = structlog.get_logger()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info(
        "donna_server_started",
        host=host,
        port=port,
        health=f"http://{host}:{port}/health",
    )

    bg_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
    if notification_tasks is not None:
        bg_tasks = [
            asyncio.create_task(
                notification_tasks.reminder_scheduler.run(),
                name="reminder_scheduler",
            ),
            asyncio.create_task(
                notification_tasks.overdue_detector.run(),
                name="overdue_detector",
            ),
            asyncio.create_task(
                notification_tasks.morning_digest.run(),
                name="morning_digest",
            ),
        ]
        logger.info("notification_background_tasks_started", count=len(bg_tasks))

    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("donna_server_stopping")

    for task in bg_tasks:
        task.cancel()
    if bg_tasks:
        await asyncio.gather(*bg_tasks, return_exceptions=True)
        logger.info("notification_background_tasks_stopped")

    await runner.cleanup()
    logger.info("donna_server_stopped")
