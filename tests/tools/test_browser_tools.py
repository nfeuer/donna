"""Tests for browser_extract_text and browser_screenshot tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from donna.skills.tools.browser_extract_text import browser_extract_text
from donna.skills.tools.browser_screenshot import browser_screenshot


@pytest.mark.asyncio
async def test_browser_extract_text_success():
    with patch("donna.skills.tools.browser_extract_text.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=AsyncMock(
            status_code=200,
            json=lambda: {
                "text": "Nike Air Max 90\n$129.99",
                "url": "https://example.com/product",
                "selector_used": "main",
                "timestamp": "2026-05-13T03:00:12Z",
                "duration_ms": 2340,
            },
            raise_for_status=lambda: None,
        ))
        mock_cls.return_value = mock_client

        result = await browser_extract_text(url="https://example.com/product", selector="main")
        assert result["text"] == "Nike Air Max 90\n$129.99"
        assert result["url"] == "https://example.com/product"


@pytest.mark.asyncio
async def test_browser_screenshot_success():
    with patch("donna.skills.tools.browser_screenshot.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=AsyncMock(
            status_code=200,
            json=lambda: {
                "file_path": "/data/browser/screenshots/test.png",
                "page_title": "Test Product",
                "url": "https://example.com/product",
                "timestamp": "2026-05-13T03:00:12Z",
                "duration_ms": 3100,
            },
            raise_for_status=lambda: None,
        ))
        mock_cls.return_value = mock_client

        result = await browser_screenshot(url="https://example.com/product")
        assert result["file_path"] == "/data/browser/screenshots/test.png"
        assert result["page_title"] == "Test Product"


@pytest.mark.asyncio
async def test_browser_extract_text_error():
    with patch("donna.skills.tools.browser_extract_text.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_cls.return_value = mock_client

        from donna.skills.tools.browser_extract_text import BrowserExtractError
        with pytest.raises(BrowserExtractError, match="Connection refused"):
            await browser_extract_text(url="https://example.com/fail")
