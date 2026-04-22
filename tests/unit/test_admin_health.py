"""Unit tests for the admin health endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from donna.api.routes.admin_health import admin_health


class TestAdminHealth:
    async def test_healthy_when_all_checks_pass(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock()

        with patch("donna.api.routes.admin_health._check_loki", return_value={"ok": True}):
            result = await admin_health(request)

        assert result["status"] == "healthy"
        assert result["checks"]["db"]["ok"] is True
        assert result["checks"]["loki"]["ok"] is True
        assert "uptime_seconds" in result
        assert "timestamp" in result

    async def test_degraded_when_db_fails(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(side_effect=Exception("connection refused"))

        with patch("donna.api.routes.admin_health._check_loki", return_value={"ok": True}):
            result = await admin_health(request)

        assert result["status"] == "degraded"
        assert result["checks"]["db"]["ok"] is False
        assert "connection refused" in result["checks"]["db"]["detail"]

    async def test_degraded_when_loki_fails(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock()

        with patch(
            "donna.api.routes.admin_health._check_loki",
            return_value={"ok": False, "detail": "status 503"},
        ):
            result = await admin_health(request)

        assert result["status"] == "degraded"
        assert result["checks"]["loki"]["ok"] is False
        assert result["checks"]["db"]["ok"] is True
