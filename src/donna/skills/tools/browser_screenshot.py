"""browser_screenshot — calls the Playwright sidecar /screenshot endpoint."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

BROWSER_SIDECAR_URL = os.environ.get("BROWSER_SIDECAR_URL", "http://donna-browser:3100")


class BrowserScreenshotError(Exception):
    """Raised when screenshot capture via the browser sidecar fails."""


async def browser_screenshot(
    url: str,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Capture a full-page screenshot via the Playwright sidecar.

    Args:
        url: The page URL to capture.
        timeout_ms: Maximum time in milliseconds to wait for the page to load.

    Returns:
        Dict with keys: ``file_path``, ``page_title``, ``url``, ``timestamp``,
        ``duration_ms``.

    Raises:
        BrowserScreenshotError: If the sidecar request fails for any reason.
    """
    payload = {"url": url, "timeout_ms": timeout_ms}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{BROWSER_SIDECAR_URL}/screenshot", json=payload)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except Exception as exc:
        logger.warning("browser_screenshot_failed", url=url, error=str(exc))
        raise BrowserScreenshotError(str(exc)) from exc

    logger.info(
        "browser_screenshot",
        url=url,
        duration_ms=data.get("duration_ms"),
        file_path=data.get("file_path"),
    )
    return data
