"""web_fetch — fetches a URL and returns structured response data."""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger()

MAX_BODY_CHARS = 200_000


class WebFetchError(Exception):
    """Raised when a web_fetch invocation fails."""


async def web_fetch(
    url: str,
    timeout_s: float = 10.0,
    method: str = "GET",
) -> dict:
    """Fetch a URL. Returns {status_code, headers, body, truncated}."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            if method.upper() == "GET":
                resp = await client.get(url)
            else:
                raise WebFetchError(f"unsupported method: {method}")
    except Exception as exc:
        logger.warning("web_fetch_failed", url=url, error=str(exc))
        raise WebFetchError(str(exc)) from exc

    body = resp.text
    truncated = False
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
        truncated = True

    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": body,
        "truncated": truncated,
    }
