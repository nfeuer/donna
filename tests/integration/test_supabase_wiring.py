"""Integration tests for SupabaseSync configuration and wiring."""

from __future__ import annotations

from donna.integrations.supabase_sync import SupabaseSync


class TestSupabaseSyncConfigured:
    def test_configured_false_without_creds(self) -> None:
        sync = SupabaseSync(supabase_url="", supabase_key="")
        assert sync.configured is False

    def test_configured_true_with_creds(self) -> None:
        sync = SupabaseSync(
            supabase_url="https://test.supabase.co",
            supabase_key="test-key",
        )
        assert sync.configured is True


class TestPushTask:
    async def test_push_task_skipped_when_not_configured(self) -> None:
        sync = SupabaseSync(supabase_url="", supabase_key="")
        # Should complete without error — fire-and-forget is silently skipped
        await sync.push_task({"id": "task-1", "title": "Test"})
        # No background tasks should have been created
        assert len(sync._background_tasks) == 0
