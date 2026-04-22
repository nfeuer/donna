"""rss_fetch — parse RSS/Atom URLs into structured items.

Thin async wrapper over `feedparser`. Offloads HTTP + parsing to a
thread. Normalizes output to a stable schema. Optional `since` (ISO-8601)
filters items server-side by published/updated timestamp.

Registered into DEFAULT_TOOL_REGISTRY at startup via
donna.skills.tools.register_default_tools (wired in Task 7).
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
import httpx
import structlog

logger = structlog.get_logger()


class RssFetchError(Exception):
    """Raised when rss_fetch cannot parse a response as RSS/Atom."""


async def _http_get(url: str, timeout_s: float) -> str:
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as c:
        resp = await c.get(url)
        resp.raise_for_status()
        return resp.text


def _parsed_time_to_iso(pt: struct_time | None) -> str | None:
    if pt is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(pt), tz=UTC).isoformat()
    except Exception:
        return None


def _item_published_iso(entry: dict[str, Any]) -> str | None:
    # feedparser normalizes to *_parsed struct_time fields when present.
    for attr in ("published_parsed", "updated_parsed"):
        val = entry.get(attr)
        if val is not None:
            iso = _parsed_time_to_iso(val)
            if iso is not None:
                return iso
    return None


def _after(iso_a: str, iso_b: str) -> bool:
    dt_a = datetime.fromisoformat(iso_a)
    dt_b = datetime.fromisoformat(iso_b)
    if dt_a.tzinfo is None:
        dt_a = dt_a.replace(tzinfo=UTC)
    if dt_b.tzinfo is None:
        dt_b = dt_b.replace(tzinfo=UTC)
    return dt_a > dt_b


async def rss_fetch(
    url: str,
    since: str | None = None,
    max_items: int = 50,
    offset: int = 0,
    timeout_s: float = 10.0,
) -> dict:
    """Fetch + parse an RSS/Atom feed.

    Pagination: `offset` skips leading filtered items. The response
    includes `has_more: bool` indicating whether additional items
    exist beyond the returned window.

    Returns
    -------
    {
        "ok": True,
        "items": [...],
        "feed_title": str,
        "feed_description": str | None,
        "has_more": bool,
    }
    """
    # Normalize string forms of null to None (Jinja can render Python None as "None").
    if since in (None, "", "None", "null"):
        since = None

    try:
        body = await _http_get(url, timeout_s)
    except Exception as exc:
        logger.warning("rss_fetch_http_failed", url=url, error=str(exc))
        raise RssFetchError(f"http: {exc}") from exc

    parsed = await asyncio.to_thread(feedparser.parse, body)
    if parsed.bozo and not parsed.entries and not getattr(parsed.feed, "title", None):
        raise RssFetchError(f"unparseable feed at {url}: {parsed.bozo_exception!r}")

    feed_title = getattr(parsed.feed, "title", "")
    feed_desc = getattr(parsed.feed, "description", None)

    # Build the full filtered list first; apply offset + max_items for the window.
    filtered: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published = _item_published_iso(entry)
        if since is not None and published is not None and not _after(published, since):
            continue
        filtered.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": published,
            "author": entry.get("author", ""),
            "summary": entry.get("summary", ""),
        })

    window = filtered[offset : offset + max_items]
    has_more = offset + len(window) < len(filtered)

    return {
        "ok": True,
        "items": window,
        "feed_title": feed_title,
        "feed_description": feed_desc,
        "has_more": has_more,
    }
