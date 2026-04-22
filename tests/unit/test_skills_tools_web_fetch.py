from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.skills.tools.web_fetch import WebFetchError, web_fetch


async def test_web_fetch_returns_structured_response():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = "<html>hello</html>"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response

    with patch("donna.skills.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await web_fetch(url="https://example.com")

    assert result["status_code"] == 200
    assert result["headers"]["content-type"] == "text/html"
    assert result["body"] == "<html>hello</html>"
    assert result["truncated"] is False


async def test_web_fetch_truncates_large_body():
    large_body = "x" * 300_000
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.text = large_body

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response

    with patch("donna.skills.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await web_fetch(url="https://example.com")

    assert len(result["body"]) == 200_000
    assert result["truncated"] is True


async def test_web_fetch_raises_on_exception():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.side_effect = RuntimeError("network down")

    with (
        patch("donna.skills.tools.web_fetch.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(WebFetchError, match="network down"),
    ):
        await web_fetch(url="https://example.com")
