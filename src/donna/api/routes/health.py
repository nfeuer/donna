"""Health endpoint — unauthenticated.

Returns service status and uptime. Used by the Docker healthcheck and
any monitoring that needs to verify the API is alive.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])

_start_time = time.monotonic()


@router.get("/health")
async def health(request: Request) -> dict:
    """Return service health. Does not require authentication."""
    db_ok = getattr(request.app.state, "db", None) is not None
    uptime_s = int(time.monotonic() - _start_time)
    return {
        "status": "healthy" if db_ok else "degraded",
        "service": "donna-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": uptime_s,
    }
