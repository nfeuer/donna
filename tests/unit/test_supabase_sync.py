"""Unit tests for SupabaseSync keep-alive."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.integrations.supabase_sync import SupabaseSync


class TestKeepAlive:
    @pytest.fixture
    def sync(self, tmp_path):
        return SupabaseSync(
            supabase_url="https://test.supabase.co",
            supabase_key="test-key",
            sync_timestamp_path=tmp_path / ".supabase_last_sync",
        )

    async def test_keepalive_sends_head_request(self, sync: SupabaseSync) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            # Run keep_alive but cancel after first iteration
            task = asyncio.create_task(sync.keep_alive(interval_hours=0.001))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_session.head.assert_called()
        call_args = mock_session.head.call_args
        assert "https://test.supabase.co/rest/v1/" in call_args[0]

    async def test_keepalive_skips_when_not_configured(self, tmp_path) -> None:
        sync = SupabaseSync(
            supabase_url="",
            supabase_key="",
            sync_timestamp_path=tmp_path / ".supabase_last_sync",
        )
        # Should return immediately without error
        await sync.keep_alive()

    async def test_keepalive_survives_errors(self, sync: SupabaseSync) -> None:
        call_count = 0

        original_head = None

        async def failing_head(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network down")

        mock_session = AsyncMock()
        mock_session.head = failing_head
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            task = asyncio.create_task(sync.keep_alive(interval_hours=0.001))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have retried at least once despite errors
        assert call_count >= 1


class TestPushTask:
    async def test_push_not_configured_is_noop(self, tmp_path) -> None:
        sync = SupabaseSync(
            supabase_url="",
            supabase_key="",
            sync_timestamp_path=tmp_path / ".supabase_last_sync",
        )
        # Should silently return without error
        await sync.push_task({"id": "test"})

    async def test_configured_returns_true(self, tmp_path) -> None:
        sync = SupabaseSync(
            supabase_url="https://test.supabase.co",
            supabase_key="test-key",
            sync_timestamp_path=tmp_path / ".supabase_last_sync",
        )
        assert sync.configured is True

    async def test_not_configured_returns_false(self, tmp_path) -> None:
        sync = SupabaseSync(
            supabase_url="",
            supabase_key="",
            sync_timestamp_path=tmp_path / ".supabase_last_sync",
        )
        assert sync.configured is False
