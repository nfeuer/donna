"""clean_html — strip non-content elements from HTML for LLM consumption.

Removes scripts, styles, SVGs, nav, footer, comments, and collapses
whitespace. Preserves product-relevant structure (prices, sizes, stock
indicators) that article extractors like trafilatura would discard.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import lxml.etree  # type: ignore[import-untyped]
import lxml.html  # type: ignore[import-untyped]
import structlog

logger = structlog.get_logger()

STRIP_TAGS = frozenset({
    "script", "style", "svg", "noscript", "link", "iframe",
    "nav", "footer", "header", "aside", "form", "picture", "source",
})

STRIP_ATTRS = frozenset({
    "class", "style", "data-testid", "data-component", "data-reactid",
    "data-automation", "data-track", "data-analytics", "onclick",
    "onload", "onerror", "srcset", "sizes",
})

DATA_ATTR_RE = re.compile(r"^data-")
WHITESPACE_RE = re.compile(r"\s{3,}")
MAX_CLEANED_CHARS = 24_000


def _clean(html: str) -> str:
    try:
        doc = lxml.html.fromstring(html)
    except Exception:
        return html

    for tag in STRIP_TAGS:
        for el in doc.iter(tag):
            el.drop_tree()

    for el in doc.iter(lxml.etree.Comment):
        el.drop_tree()

    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        removable = [
            a for a in el.attrib
            if a in STRIP_ATTRS or DATA_ATTR_RE.match(a)
        ]
        for a in removable:
            del el.attrib[a]

    text = lxml.html.tostring(doc, encoding="unicode", method="html")
    text = WHITESPACE_RE.sub("\n", text)
    if len(text) > MAX_CLEANED_CHARS:
        text = text[:MAX_CLEANED_CHARS]
    return text


async def clean_html(html: str) -> dict[str, Any]:
    """Strip non-content HTML elements.

    Returns {"cleaned": str, "original_len": int, "cleaned_len": int}.
    """
    original_len = len(html)
    cleaned = await asyncio.to_thread(_clean, html)
    logger.info(
        "clean_html",
        original_len=original_len,
        cleaned_len=len(cleaned),
        reduction_pct=round((1 - len(cleaned) / max(original_len, 1)) * 100, 1),
    )
    return {
        "cleaned": cleaned,
        "original_len": original_len,
        "cleaned_len": len(cleaned),
    }
