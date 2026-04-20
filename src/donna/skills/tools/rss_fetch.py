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
from datetime import datetime, timezone
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
        return datetime.fromtimestamp(calendar.timegm(pt), tz=timezone.utc).isoformat()
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
        dt_a = dt_a.replace(tzinfo=timezone.utc)
    if dt_b.tzinfo is None:
        dt_b = dt_b.replace(tzinfo=timezone.utc)
    return dt_a > dt_b


async def rss_fetch(
    url: str,
    since: str | None = None,
    max_items: int = 50,
    timeout_s: float = 10.0,
) -> dict:
    """Fetch + parse an RSS/Atom feed.

    Returns
    -------
    {
        "ok": True,
        "items": [{"title", "link", "published", "author", "summary"}, ...],
        "feed_title": str,
        "feed_description": str | None,
    }

    Raises
    ------
    RssFetchError — on unparseable / empty non-feed response.
    """
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

    items: list[dict[str, Any]] = []
    for entry in parsed.entries[: max_items * 4]:  # over-read to survive since-filter
        published = _item_published_iso(entry)
        # When `since` is set and the item has no published/updated timestamp,
        # we include it rather than skip it. Rationale: an undated item could be
        # newer than `since`; skipping would risk missing real news. Downstream
        # skills may dedup via their own classification step.
        if since is not None and published is not None and not _after(published, since):
            continue
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": published,
            "author": entry.get("author", ""),
            "summary": entry.get("summary", ""),
        })
        if len(items) >= max_items:
            break

    return {
        "ok": True,
        "items": items,
        "feed_title": feed_title,
        "feed_description": feed_desc,
    }
