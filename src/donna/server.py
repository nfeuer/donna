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
import os
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from aiohttp import web

if TYPE_CHECKING:
    from donna.integrations.sms_router import SmsRouter
    from donna.integrations.twilio_sms import TwilioSMS
    from donna.notifications.digest import MorningDigest
    from donna.notifications.escalation import EscalationManager
    from donna.notifications.overdue import OverdueDetector
    from donna.notifications.reminders import ReminderScheduler


@dataclasses.dataclass
class NotificationTasks:
    """Container for Slice-5 and Slice-7 background task runners."""

    reminder_scheduler: ReminderScheduler
    overdue_detector: OverdueDetector
    morning_digest: MorningDigest
    escalation_manager: EscalationManager | None = None


async def health_handler(request: web.Request) -> web.Response:
    """GET /health — liveness probe used by Docker healthcheck."""
    return web.json_response(
        {
            "status": "healthy",
            "service": "donna-orchestrator",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )


def make_sms_inbound_handler(
    twilio_sms: TwilioSMS,
    sms_router: SmsRouter,
    webhook_url: str,
) -> web.RequestHandler:
    """Return a handler for POST /sms/inbound that validates Twilio signatures."""

    async def sms_inbound_handler(request: web.Request) -> web.Response:
        """POST /sms/inbound — Twilio webhook for inbound SMS."""
        logger = structlog.get_logger()

        # Validate Twilio signature.
        signature = request.headers.get("X-Twilio-Signature", "")
        params = dict(await request.post())
        str_params: dict[str, str] = {k: str(v) for k, v in params.items()}

        if not twilio_sms.verify_signature(webhook_url, str_params, signature):
            logger.warning("sms_inbound_invalid_signature")
            return web.Response(status=403, text="Forbidden")

        from_number = str_params.get("From", "")
        body = str_params.get("Body", "")

        logger.info("sms_inbound_received", from_number=from_number, body_len=len(body))

        try:
            await sms_router.route_inbound(from_number=from_number, body=body)
        except Exception:
            logger.exception("sms_inbound_routing_failed")

        # Return empty TwiML — no auto-reply.
        return web.Response(
            content_type="text/xml",
            text='<?xml version="1.0" encoding="UTF-8"?><Response/>',
        )

    return sms_inbound_handler  # type: ignore[return-value]


def create_app(
    twilio_sms: TwilioSMS | None = None,
    sms_router: SmsRouter | None = None,
) -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()
    app.router.add_get("/health", health_handler)

    if twilio_sms is not None and sms_router is not None:
        webhook_url = os.environ.get("TWILIO_WEBHOOK_URL", "")
        app.router.add_post(
            "/sms/inbound",
            make_sms_inbound_handler(twilio_sms, sms_router, webhook_url),
        )

    return app


async def run_server(
    host: str = "0.0.0.0",  # noqa: S104
    port: int = 8100,
    notification_tasks: NotificationTasks | None = None,
    twilio_sms: TwilioSMS | None = None,
    sms_router: SmsRouter | None = None,
) -> None:
    """Start the aiohttp server and block until shutdown signal received.

    If notification_tasks is supplied, the background loops are started as
    asyncio tasks and cancelled cleanly on shutdown.
    """
    logger = structlog.get_logger()

    app = create_app(twilio_sms=twilio_sms, sms_router=sms_router)
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
        if notification_tasks.escalation_manager is not None:
            bg_tasks.append(
                asyncio.create_task(
                    notification_tasks.escalation_manager.check_and_advance(),
                    name="escalation_manager",
                )
            )
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
