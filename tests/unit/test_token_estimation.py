"""Unit tests for the char-based token estimation helper."""

from donna.models.tokens import estimate_tokens


def test_estimate_tokens_empty_string() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_short_string() -> None:
    # "hello world" = 11 chars → 11 // 4 = 2
    assert estimate_tokens("hello world") == 2


def test_estimate_tokens_rounds_down() -> None:
    # 7 chars → 7 // 4 = 1
    assert estimate_tokens("abcdefg") == 1


def test_estimate_tokens_longer_string() -> None:
    # 400 chars → 100
    assert estimate_tokens("x" * 400) == 100


def test_estimate_tokens_non_ascii() -> None:
    # Heuristic is char-based, not byte-based; 4 chars = 1 token regardless.
    assert estimate_tokens("日本語です") == 1
