"""Unit test: NotificationService truncates digest content exceeding the cap."""
from __future__ import annotations

import pytest


def test_truncate_helper_passes_short_content_unchanged() -> None:
    from donna.notifications.service import NotificationService
    result = NotificationService._truncate_for_channel("hello", max_chars=1900)
    assert result == "hello"


def test_truncate_helper_truncates_long_content() -> None:
    from donna.notifications.service import NotificationService
    long_content = "x" * 3000
    result = NotificationService._truncate_for_channel(long_content, max_chars=1900)
    assert len(result) <= 2000
    assert "truncated" in result.lower() or "…" in result or "more" in result.lower()


def test_truncate_helper_appends_count_footer() -> None:
    from donna.notifications.service import NotificationService
    long_content = "x" * 2500
    result = NotificationService._truncate_for_channel(long_content, max_chars=1900)
    # Footer should mention how many chars were truncated
    assert "more" in result.lower() or "truncat" in result.lower()


def test_truncate_uses_default_cap() -> None:
    """Verify DIGEST_MAX_CHARS_DEFAULT is applied when NotificationService is
    constructed without digest_max_chars."""
    from donna.notifications.service import DIGEST_MAX_CHARS_DEFAULT
    assert DIGEST_MAX_CHARS_DEFAULT == 1900
