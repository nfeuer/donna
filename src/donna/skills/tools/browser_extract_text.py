"""browser_extract_text — calls the Playwright sidecar /extract-text endpoint."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

BROWSER_SIDECAR_URL = os.environ.get("BROWSER_SIDECAR_URL", "http://donna-browser:3100")


class BrowserExtractError(Exception):
    """Raised when text extraction via the browser sidecar fails."""


async def browser_extract_text(
    url: str,
    selector: str = "body",
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Extract text from a URL via the Playwright sidecar.

    Args:
        url: The page URL to load and extract text from.
        selector: CSS selector to scope the extraction (default: ``body``).
        timeout_ms: Maximum time in milliseconds to wait for the page to load.

    Returns:
        Dict with keys: ``text``, ``url``, ``selector_used``, ``timestamp``,
        ``duration_ms``.

    Raises:
        BrowserExtractError: If the sidecar request fails for any reason.
    """
    payload = {"url": url, "selector": selector, "timeout_ms": timeout_ms}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{BROWSER_SIDECAR_URL}/extract-text", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("browser_extract_text_failed", url=url, error=str(exc))
        raise BrowserExtractError(str(exc)) from exc

    logger.info(
        "browser_extract_text",
        url=url,
        duration_ms=data.get("duration_ms"),
        text_length=len(data.get("text", "")),
    )
    return data
