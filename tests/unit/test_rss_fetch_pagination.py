"""Unit test: rss_fetch supports offset + has_more."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import importlib
import sys

import donna.skills.tools.rss_fetch  # noqa: F401 — ensure submodule is imported
rss_mod = sys.modules["donna.skills.tools.rss_fetch"]


_FEED_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>F</title>
<item><title>i0</title><link>l0</link></item>
<item><title>i1</title><link>l1</link></item>
<item><title>i2</title><link>l2</link></item>
<item><title>i3</title><link>l3</link></item>
<item><title>i4</title><link>l4</link></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_offset_skips_leading_items() -> None:
    async def _fake_get(url: str, timeout_s: float) -> str:
        return _FEED_BODY

    with patch.object(rss_mod, "_http_get", _fake_get):
        result = await rss_mod.rss_fetch(url="http://x", offset=2, max_items=2)
    titles = [it["title"] for it in result["items"]]
    assert titles == ["i2", "i3"]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_has_more_false_when_exhausted() -> None:
    async def _fake_get(url: str, timeout_s: float) -> str:
        return _FEED_BODY

    with patch.object(rss_mod, "_http_get", _fake_get):
        result = await rss_mod.rss_fetch(url="http://x", offset=3, max_items=10)
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_default_offset_zero() -> None:
    async def _fake_get(url: str, timeout_s: float) -> str:
        return _FEED_BODY

    with patch.object(rss_mod, "_http_get", _fake_get):
        result = await rss_mod.rss_fetch(url="http://x", max_items=3)
    titles = [it["title"] for it in result["items"]]
    assert titles == ["i0", "i1", "i2"]
    assert result["has_more"] is True
