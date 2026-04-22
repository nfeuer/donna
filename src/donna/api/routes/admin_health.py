"""Admin health endpoint for the Management GUI dashboard.

Checks DB connectivity, Loki reachability, and reports uptime.
Unlike the root /health endpoint (liveness probe), this provides
richer component-level detail for the dashboard UI.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp
import structlog
from fastapi import Request

from donna.api.auth import admin_router

logger = structlog.get_logger()
router = admin_router()

_start_time = time.monotonic()
_LOKI_URL = os.environ.get("DONNA_LOKI_URL", "http://donna-loki:3100")
_OLLAMA_URL = os.environ.get("DONNA_OLLAMA_URL", "http://donna-ollama:11434")


async def _check_db(conn: Any) -> dict[str, Any]:
    """Verify the DB connection responds within 2 seconds."""
    try:
        async with asyncio.timeout(2):
            await conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


async def _check_loki() -> dict[str, Any]:
    """HTTP GET to Loki /ready with a 3-second timeout."""
    try:
        async with aiohttp.ClientSession() as session, session.get(
            f"{_LOKI_URL}/ready",
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            if resp.status == 200:
                return {"ok": True}
            return {"ok": False, "detail": f"status {resp.status}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


async def _check_ollama(check_enabled: bool = True) -> dict[str, Any] | None:
    """HTTP GET to Ollama /api/tags with a 3-second timeout.

    Returns None if DONNA_OLLAMA_URL is empty (Ollama not deployed) or check is disabled.
    """
    if not check_enabled or not _OLLAMA_URL:
        return None
    try:
        async with aiohttp.ClientSession() as session, session.get(
            f"{_OLLAMA_URL}/api/tags",
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                models = [m["name"] for m in data.get("models", [])]
                return {"ok": True, "models": models}
            return {"ok": False, "detail": f"status {resp.status}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@router.get("/health")
async def admin_health(request: Request) -> dict[str, Any]:
    """Single-glance system health for the dashboard.

    Checks DB, Loki, and Ollama connectivity, reports uptime.
    Returns 200 always (the status field indicates health).
    """
    conn = request.app.state.db.connection

    gw_config = getattr(request.app.state, "llm_gateway_config", None)
    ollama_check_enabled = gw_config.ollama_health_check if gw_config else True

    db_check, loki_check, ollama_check = await asyncio.gather(
        _check_db(conn),
        _check_loki(),
        _check_ollama(check_enabled=ollama_check_enabled),
    )

    checks: dict[str, Any] = {
        "db": db_check,
        "loki": loki_check,
    }
    if ollama_check is not None:
        checks["ollama"] = ollama_check

    healthy = all(v["ok"] for v in checks.values())
    status = "healthy" if healthy else "degraded"
    uptime_s = int(time.monotonic() - _start_time)

    logger.info(
        "admin_health_check",
        event_type="system.admin_health_check",
        status=status,
        checks={k: v["ok"] for k, v in checks.items()},
    )

    return {
        "status": status,
        "checks": checks,
        "uptime_seconds": uptime_s,
        "timestamp": datetime.now(UTC).isoformat(),
    }
