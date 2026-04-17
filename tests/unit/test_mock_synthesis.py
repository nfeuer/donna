"""Tests for donna.skills.mock_synthesis.cache_to_mocks."""

from __future__ import annotations

from donna.skills.mock_synthesis import cache_to_mocks


def test_cache_to_mocks_empty() -> None:
    assert cache_to_mocks({}) == {}


def test_cache_to_mocks_web_fetch_uses_rule() -> None:
    cache = {
        "cache_abc": {
            "tool": "web_fetch",
            "args": {"url": "https://example.com", "timeout_s": 10, "headers": {}},
            "result": {"status": 200, "body": "<html>x</html>"},
        },
    }
    mocks = cache_to_mocks(cache)
    # Fingerprint rule strips timeout_s/headers — keys on {"url": ...} only.
    assert 'web_fetch:{"url":"https://example.com"}' in mocks
    assert mocks['web_fetch:{"url":"https://example.com"}'] == {
        "status": 200, "body": "<html>x</html>",
    }


def test_cache_to_mocks_unknown_tool_canonical_json() -> None:
    cache = {
        "cache_x": {
            "tool": "some_tool",
            "args": {"b": 2, "a": 1},
            "result": {"ok": True},
        },
    }
    mocks = cache_to_mocks(cache)
    assert 'some_tool:{"a":1,"b":2}' in mocks


def test_cache_to_mocks_skips_malformed() -> None:
    cache = {
        "cache_good": {"tool": "web_fetch", "args": {"url": "https://x"}, "result": {"ok": 1}},
        "cache_no_tool": {"args": {}, "result": {}},
        "cache_no_result": {"tool": "web_fetch", "args": {"url": "https://y"}},
        "cache_not_dict": "garbage",
    }
    mocks = cache_to_mocks(cache)
    assert len(mocks) == 1
    assert any("https://x" in k for k in mocks)


def test_cache_to_mocks_preserves_result_shape_and_deep_copies() -> None:
    result = {"status": 200, "body": "<html/>", "headers": {"content-type": "text/html"}}
    cache = {"c1": {"tool": "web_fetch", "args": {"url": "https://z"}, "result": result}}
    mocks = cache_to_mocks(cache)
    # Same content, different object — deep copy.
    assert next(iter(mocks.values())) == result
    assert next(iter(mocks.values())) is not result
