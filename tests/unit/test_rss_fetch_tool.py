"""Tests for donna.skills.tools.rss_fetch — RSS/Atom parsing + since filter."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from donna.skills.tools.rss_fetch import rss_fetch, RssFetchError


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Test Feed</title>
<description>A feed</description>
<item>
  <title>Article One</title>
  <link>https://example.com/1</link>
  <pubDate>Mon, 20 Apr 2026 08:00:00 GMT</pubDate>
  <author>alice@example.com (Alice)</author>
  <description>First summary</description>
</item>
<item>
  <title>Article Two</title>
  <link>https://example.com/2</link>
  <pubDate>Sun, 19 Apr 2026 08:00:00 GMT</pubDate>
  <description>Older summary</description>
</item>
</channel></rss>
"""


@pytest.mark.asyncio
async def test_rss_fetch_parses_valid_rss_and_returns_items():
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(url="https://example.com/feed")
    assert result["ok"] is True
    assert result["feed_title"] == "Test Feed"
    titles = [i["title"] for i in result["items"]]
    assert "Article One" in titles
    assert "Article Two" in titles


@pytest.mark.asyncio
async def test_rss_fetch_since_filter_drops_older_items():
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(
            url="https://example.com/feed",
            since="2026-04-20T00:00:00+00:00",
        )
    titles = [i["title"] for i in result["items"]]
    assert titles == ["Article One"]


@pytest.mark.asyncio
async def test_rss_fetch_max_items_caps_result():
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(url="https://example.com/feed", max_items=1)
    assert len(result["items"]) == 1


@pytest.mark.asyncio
async def test_rss_fetch_empty_feed_returns_empty_items():
    empty = """<?xml version="1.0"?><rss version="2.0"><channel>
    <title>Empty</title></channel></rss>"""
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=empty):
        result = await rss_fetch(url="https://example.com/feed")
    assert result["ok"] is True
    assert result["items"] == []


@pytest.mark.asyncio
async def test_rss_fetch_malformed_raises():
    with patch(
        "donna.skills.tools.rss_fetch._http_get",
        return_value="not xml at all",
    ):
        with pytest.raises(RssFetchError, match="unparseable feed"):
            await rss_fetch(url="https://example.com/feed")


@pytest.mark.asyncio
async def test_rss_fetch_http_error_wraps_in_rss_fetch_error():
    with patch(
        "donna.skills.tools.rss_fetch._http_get",
        side_effect=Exception("connection refused"),
    ):
        with pytest.raises(RssFetchError, match="http:"):
            await rss_fetch(url="https://unreachable.example.com/feed")


@pytest.mark.asyncio
async def test_rss_fetch_atom_feed():
    atom = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Feed</title>
<entry>
  <title>Atom Article</title>
  <link href="https://example.com/atom/1"/>
  <updated>2026-04-20T10:00:00Z</updated>
  <summary>Atom summary</summary>
</entry>
</feed>
"""
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=atom):
        result = await rss_fetch(url="https://example.com/atom")
    titles = [i["title"] for i in result["items"]]
    assert "Atom Article" in titles


@pytest.mark.asyncio
async def test_rss_fetch_published_timestamp_is_utc_correct():
    """Regression: mktime (vs calendar.timegm) silently applies host TZ offset."""
    with patch("donna.skills.tools.rss_fetch._http_get", return_value=RSS_SAMPLE):
        result = await rss_fetch(url="https://example.com/feed")
    pub_by_title = {i["title"]: i["published"] for i in result["items"]}
    # RSS_SAMPLE has "Mon, 20 Apr 2026 08:00:00 GMT" for Article One.
    assert pub_by_title["Article One"] == "2026-04-20T08:00:00+00:00"
    assert pub_by_title["Article Two"] == "2026-04-19T08:00:00+00:00"
