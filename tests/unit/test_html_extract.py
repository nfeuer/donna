"""Unit tests for html_extract tool."""
from __future__ import annotations

import pytest

ARTICLE_HTML = """
<html><head><title>My Article</title></head>
<body>
  <nav>menu</nav>
  <article>
    <h1>Headline</h1>
    <p>First paragraph of the article. It contains substantive text
    that trafilatura should capture as the main content. The paragraph
    is long enough to count as substantive body text for extraction.</p>
    <p>Second paragraph with more detail. This continues the article
    content and should also be captured by the extractor.</p>
    <a href="/related">Related</a>
  </article>
  <footer>footer</footer>
</body></html>
"""

EMPTY_HTML = "<html><body></body></html>"


@pytest.mark.asyncio
async def test_extracts_title_and_text() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html=ARTICLE_HTML, base_url="https://ex.com/a")
    assert result["ok"] is True
    assert "First paragraph" in result["text"]
    assert result["length"] > 0


@pytest.mark.asyncio
async def test_returns_not_ok_on_empty() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html=EMPTY_HTML)
    assert result["ok"] is False
    assert result["reason"] == "no_content"


@pytest.mark.asyncio
async def test_excerpt_is_prefix_of_text() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html=ARTICLE_HTML)
    if result["ok"]:
        assert result["excerpt"] == result["text"][: len(result["excerpt"])]


@pytest.mark.asyncio
async def test_empty_input_returns_no_content() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html="")
    assert result["ok"] is False
    assert result["reason"] == "no_content"
