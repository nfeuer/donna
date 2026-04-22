"""html_extract — extract article text + metadata from HTML using trafilatura.

Does NOT fetch. Chain `web_fetch` → `html_extract`, passing the fetched body
as `html`. Keeping fetch + extract separate preserves testability.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
import trafilatura

logger = structlog.get_logger()

EXCERPT_CHARS = 280


async def html_extract(html: str, base_url: str | None = None) -> dict[str, Any]:
    """Extract article content.

    Returns
    -------
    On success:
        {"ok": True, "title": str, "text": str, "excerpt": str,
         "links": list[dict], "length": int}
    On empty/no-content:
        {"ok": False, "reason": "no_content"}
    """
    if not html or not html.strip():
        return {"ok": False, "reason": "no_content"}

    def _run():
        return trafilatura.extract(
            html,
            url=base_url,
            output_format="json",
            with_metadata=True,
            include_links=True,
        )

    try:
        raw = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.warning("html_extract_failed", error=str(exc))
        return {"ok": False, "reason": "extractor_error"}

    if raw is None:
        return {"ok": False, "reason": "no_content"}

    data = json.loads(raw)
    text = (data.get("text") or "").strip()
    if not text:
        return {"ok": False, "reason": "no_content"}

    title = (data.get("title") or "").strip()
    links_raw = data.get("links") or []
    if isinstance(links_raw, list):
        links = [
            {"text": lnk.get("text", "") or "", "href": lnk.get("url", "") or ""}
            for lnk in links_raw
            if isinstance(lnk, dict)
        ]
    else:
        links = []

    return {
        "ok": True,
        "title": title,
        "text": text,
        "excerpt": text[:EXCERPT_CHARS],
        "links": links,
        "length": len(text),
    }
