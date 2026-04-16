"""Tests for donna.skills.tool_fingerprint."""

from __future__ import annotations

import pytest
from donna.skills.tool_fingerprint import fingerprint


def test_web_fetch_uses_only_url() -> None:
    fp1 = fingerprint("web_fetch", {
        "url": "https://example.com", "timeout_s": 10, "headers": {"User-Agent": "a"},
    })
    fp2 = fingerprint("web_fetch", {
        "url": "https://example.com", "timeout_s": 30, "headers": {"User-Agent": "b"},
    })
    assert fp1 == fp2
    assert fp1.startswith("web_fetch:")


def test_web_fetch_different_urls_differ() -> None:
    fp1 = fingerprint("web_fetch", {"url": "https://a.com"})
    fp2 = fingerprint("web_fetch", {"url": "https://b.com"})
    assert fp1 != fp2


def test_gmail_read_uses_only_message_id() -> None:
    fp1 = fingerprint("gmail_read", {"message_id": "m1", "label_ids": ["INBOX"]})
    fp2 = fingerprint("gmail_read", {"message_id": "m1"})
    assert fp1 == fp2


def test_gmail_send_uses_to_subject_body() -> None:
    fp1 = fingerprint("gmail_send", {
        "to": "a@b.com", "subject": "s", "body": "x", "draft_id": "d1",
    })
    fp2 = fingerprint("gmail_send", {
        "to": "a@b.com", "subject": "s", "body": "x", "draft_id": "d2",
    })
    assert fp1 == fp2


def test_default_rule_canonical_json_all_args() -> None:
    fp1 = fingerprint("unknown_tool", {"b": 2, "a": 1})
    fp2 = fingerprint("unknown_tool", {"a": 1, "b": 2})
    assert fp1 == fp2
    assert fp1.startswith("unknown_tool:")


def test_default_rule_different_args_differ() -> None:
    fp1 = fingerprint("unknown_tool", {"a": 1})
    fp2 = fingerprint("unknown_tool", {"a": 2})
    assert fp1 != fp2


def test_missing_required_field_raises() -> None:
    with pytest.raises(KeyError):
        fingerprint("web_fetch", {})
