"""Admin-accessible LLM queue endpoints for the dashboard UI.

The primary LLM routes (``/llm/*``) use service-key auth for homelab
inter-service calls.  The dashboard needs read-only access to queue
status and the SSE stream, so we expose thin wrappers here under the
admin prefix.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, cast

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from donna.api.auth import admin_router

router = admin_router()


@router.get("/llm/queue/status")
async def admin_llm_queue_status(request: Request) -> dict[str, Any]:
    """Live queue status for the admin dashboard."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        return {
            "current_request": None,
            "internal_queue": {"pending": 0, "next_items": []},
            "external_queue": {"pending": 0, "next_items": []},
            "stats_24h": {
                "internal_completed": 0,
                "external_completed": 0,
                "external_interrupted": 0,
            },
            "rate_limits": {},
            "mode": "active",
        }
    return cast(dict[str, Any], queue.get_status())


@router.get("/llm/queue/stream")
async def admin_llm_queue_stream(request: Request) -> StreamingResponse:
    """SSE stream of queue state changes for the admin dashboard."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            status = queue.get_status()
            yield f"data: {json.dumps(status)}\n\n"

            while True:
                try:
                    async with asyncio.timeout(15):
                        async with queue.state_changed:
                            await queue.state_changed.wait()
                    status = queue.get_status()
                    yield f"data: {json.dumps(status)}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
