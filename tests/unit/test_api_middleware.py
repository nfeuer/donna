"""Unit tests for the request logging middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from donna.api import RequestLoggingMiddleware


class TestRequestLoggingMiddleware:
    async def test_logs_request_with_correct_fields(self) -> None:
        middleware = RequestLoggingMiddleware(app=AsyncMock())

        mock_request = AsyncMock()
        mock_request.method = "GET"
        mock_request.url.path = "/admin/dashboard"

        mock_response = AsyncMock()
        mock_response.status_code = 200

        call_next = AsyncMock(return_value=mock_response)

        with patch("donna.api.logger") as mock_logger:
            result = await middleware.dispatch(mock_request, call_next)

        assert result == mock_response
        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs.kwargs["event_type"] == "admin.request"
        assert call_kwargs.kwargs["method"] == "GET"
        assert call_kwargs.kwargs["path"] == "/admin/dashboard"
        assert call_kwargs.kwargs["status_code"] == 200
        assert "duration_ms" in call_kwargs.kwargs

    async def test_api_component_for_non_admin_path(self) -> None:
        middleware = RequestLoggingMiddleware(app=AsyncMock())

        mock_request = AsyncMock()
        mock_request.method = "POST"
        mock_request.url.path = "/tasks"

        mock_response = AsyncMock()
        mock_response.status_code = 201

        call_next = AsyncMock(return_value=mock_response)

        with patch("donna.api.logger") as mock_logger:
            await middleware.dispatch(mock_request, call_next)

        call_kwargs = mock_logger.info.call_args
        assert call_kwargs.kwargs["component"] == "api"
        assert call_kwargs.kwargs["event_type"] == "api.request"
