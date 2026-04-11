"""Tests for per-caller rate limiting."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from donna.llm.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_under_limit(self) -> None:
        rl = RateLimiter(default_rpm=5, default_rph=100, caller_limits={})
        for _ in range(5):
            assert rl.check("test-caller") is True

    def test_rejects_over_minute_limit(self) -> None:
        rl = RateLimiter(default_rpm=2, default_rph=100, caller_limits={})
        assert rl.check("test-caller") is True
        assert rl.check("test-caller") is True
        assert rl.check("test-caller") is False

    def test_per_caller_limits_override_default(self) -> None:
        rl = RateLimiter(
            default_rpm=2,
            default_rph=100,
            caller_limits={"fast-caller": {"requests_per_minute": 10}},
        )
        for _ in range(10):
            assert rl.check("fast-caller") is True
        # But default still limited
        rl.check("slow-caller")
        rl.check("slow-caller")
        assert rl.check("slow-caller") is False

    def test_separate_callers_have_separate_counters(self) -> None:
        rl = RateLimiter(default_rpm=1, default_rph=100, caller_limits={})
        assert rl.check("caller-a") is True
        assert rl.check("caller-a") is False
        assert rl.check("caller-b") is True  # separate counter

    def test_rejection_count_tracking(self) -> None:
        rl = RateLimiter(default_rpm=1, default_rph=100, caller_limits={})
        rl.check("test-caller")
        rl.check("test-caller")  # rejected
        rl.check("test-caller")  # rejected
        assert rl.recent_rejections("test-caller", window_seconds=300) == 2

    def test_get_usage_for_caller(self) -> None:
        rl = RateLimiter(default_rpm=10, default_rph=100, caller_limits={})
        rl.check("test-caller")
        rl.check("test-caller")
        usage = rl.get_usage("test-caller")
        assert usage["minute_count"] == 2
        assert usage["minute_limit"] == 10

    def test_rebuild_from_records(self) -> None:
        """Simulate rebuilding counters from invocation_log on startup."""
        rl = RateLimiter(default_rpm=10, default_rph=5, caller_limits={})
        now = time.monotonic()
        # Simulate 5 recent calls from a caller
        rl.rebuild_from_records("busy-caller", call_count_last_hour=5)
        assert rl.check("busy-caller") is False  # at hour limit

    def test_update_limits(self) -> None:
        """Hot-reload: update limits without losing counters."""
        rl = RateLimiter(default_rpm=5, default_rph=100, caller_limits={})
        rl.check("test-caller")
        rl.check("test-caller")
        rl.update_limits(default_rpm=10, default_rph=200, caller_limits={})
        # Counters preserved, but new limits apply
        assert rl.check("test-caller") is True  # still under new limit
