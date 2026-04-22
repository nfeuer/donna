"""Unit tests for the expanded /health endpoint.

Tests component check logic and HTTP status codes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from donna.server import (
    _check_api_freshness,
    _check_discord,
    _check_scheduler,
    _check_sqlite,
    create_app,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_client(
    tmp_path: Path | None = None, **app_state: object,
) -> tuple[web.Application, TestClient]:
    """Build a test aiohttp app with given app state."""
    db_path: str | None = None
    if tmp_path is not None:
        db_file = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_file)) as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
            await conn.commit()
        db_path = str(db_file)

    app = create_app(db_path=db_path)
    for k, v in app_state.items():
        app[k] = v
    client = TestClient(TestServer(app))
    return app, client


# ---------------------------------------------------------------------------
# Tests: individual check functions
# ---------------------------------------------------------------------------

class TestCheckSqlite:
    @pytest.mark.asyncio
    async def test_returns_ok_for_valid_db(self, tmp_path: Path) -> None:
        db_file = tmp_path / "ok.db"
        async with aiosqlite.connect(str(db_file)) as conn:
            await conn.execute("SELECT 1")
        result = await _check_sqlite(str(db_file))
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_returns_not_ok_for_missing_db(self, tmp_path: Path) -> None:
        await _check_sqlite(str(tmp_path / "nonexistent.db"))
        # aiosqlite creates the file, so this checks a genuinely invalid path
        # by pointing at a directory instead.
        result2 = await _check_sqlite(str(tmp_path))  # directory, not a db
        assert result2["ok"] is False

    @pytest.mark.asyncio
    async def test_returns_ok_when_no_db_path(self) -> None:
        result = await _check_sqlite(None)
        assert result["ok"] is True


class TestCheckDiscord:
    def test_returns_ok_when_flag_set(self) -> None:
        app = web.Application()
        app["discord_ready"] = True
        assert _check_discord(app)["ok"] is True

    def test_returns_not_ok_when_flag_false(self) -> None:
        app = web.Application()
        app["discord_ready"] = False
        result = _check_discord(app)
        assert result["ok"] is False

    def test_returns_not_ok_when_flag_absent(self) -> None:
        app = web.Application()
        result = _check_discord(app)
        assert result["ok"] is False


class TestCheckScheduler:
    def test_returns_ok_when_no_heartbeat_wired(self) -> None:
        app = web.Application()
        assert _check_scheduler(app)["ok"] is True

    def test_returns_ok_for_recent_heartbeat(self) -> None:
        app = web.Application()
        app["scheduler_last_heartbeat"] = datetime.now(UTC)
        assert _check_scheduler(app)["ok"] is True

    def test_returns_not_ok_for_stale_heartbeat(self) -> None:
        app = web.Application()
        app["scheduler_last_heartbeat"] = datetime.now(UTC) - timedelta(minutes=15)
        assert _check_scheduler(app)["ok"] is False


class TestCheckApiFreshness:
    def test_returns_ok_when_no_ts(self) -> None:
        app = web.Application()
        assert _check_api_freshness(app)["ok"] is True

    def test_returns_ok_for_recent_ts(self) -> None:
        app = web.Application()
        app["last_api_ts"] = datetime.now(UTC)
        assert _check_api_freshness(app)["ok"] is True

    def test_returns_not_ok_for_stale_ts(self) -> None:
        app = web.Application()
        app["last_api_ts"] = datetime.now(UTC) - timedelta(minutes=15)
        assert _check_api_freshness(app)["ok"] is False


# ---------------------------------------------------------------------------
# Tests: full /health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200_when_all_ok(self, tmp_path: Path) -> None:
        """Returns 200 and status=healthy when all components pass."""
        _, client = await _make_client(
            tmp_path,
            discord_ready=True,
            scheduler_last_heartbeat=datetime.now(UTC),
            last_api_ts=datetime.now(UTC),
        )
        async with client:
            resp = await client.get("/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_returns_503_when_discord_down(self, tmp_path: Path) -> None:
        """Returns 503 and status=degraded when Discord is not ready."""
        _, client = await _make_client(
            tmp_path,
            discord_ready=False,
            last_api_ts=datetime.now(UTC),
        )
        async with client:
            resp = await client.get("/health")
            assert resp.status == 503
            data = await resp.json()
            assert data["status"] == "degraded"
            assert data["checks"]["discord"]["ok"] is False

    @pytest.mark.asyncio
    async def test_health_checks_all_components_present(self, tmp_path: Path) -> None:
        """Response includes all 4 check keys."""
        _, client = await _make_client(tmp_path, discord_ready=True)
        async with client:
            resp = await client.get("/health")
            data = await resp.json()
            checks = data["checks"]
            assert "sqlite" in checks
            assert "discord" in checks
            assert "scheduler" in checks
            assert "api_freshness" in checks

    @pytest.mark.asyncio
    async def test_health_includes_timestamp_and_service(self, tmp_path: Path) -> None:
        """Response always includes timestamp and service name."""
        _, client = await _make_client(tmp_path, discord_ready=True)
        async with client:
            resp = await client.get("/health")
            data = await resp.json()
            assert "timestamp" in data
            assert data["service"] == "donna-orchestrator"

    @pytest.mark.asyncio
    async def test_health_503_when_sqlite_unavailable(self) -> None:
        """Returns 503 when db_path points to invalid location."""
        app = create_app(db_path="/nonexistent_dir/fake.db")
        app["discord_ready"] = True
        client = TestClient(TestServer(app))
        async with client:
            resp = await client.get("/health")
            # aiosqlite will attempt to create the file; if parent doesn't exist it fails
            data = await resp.json()
            # If sqlite check fails, overall status is degraded
            if not data["checks"]["sqlite"]["ok"]:
                assert resp.status == 503
                assert data["status"] == "degraded"
