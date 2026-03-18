"""Donna aiohttp web server.

Exposes the health endpoint and serves as the process entrypoint for
the orchestrator container. Business logic lives elsewhere; this module
is pure infrastructure.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime

import structlog
from aiohttp import web


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


async def run_server(host: str = "0.0.0.0", port: int = 8100) -> None:  # noqa: S104
    """Start the aiohttp server and block until shutdown signal received."""
    logger = structlog.get_logger()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info("donna_server_started", host=host, port=port, health=f"http://{host}:{port}/health")

    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("donna_server_stopping")
    await runner.cleanup()
    logger.info("donna_server_stopped")
