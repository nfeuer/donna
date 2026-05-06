"""Lint tests — secrets scanner (slice 22 §10.5 row 2)."""

from __future__ import annotations

from donna.cost.tool_lint.secrets import scan_for_secrets


def test_flags_anthropic_key_pattern():
    text = 'KEY = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"\n'
    failures = scan_for_secrets(text, "x.py")
    assert any(f.rule.startswith("secrets:anthropic_api_key") for f in failures)


def test_flags_slack_bot_token():
    text = 'TOKEN = "xoxb-123456-ABCDEFG-xyz"\n'
    failures = scan_for_secrets(text, "x.py")
    assert any(f.rule.startswith("secrets:slack_bot_token") for f in failures)


def test_flags_pem_private_key_header():
    text = "-----BEGIN RSA PRIVATE KEY-----\n"
    failures = scan_for_secrets(text, "x.py")
    assert any(f.rule.startswith("secrets:private_key_header") for f in failures)


def test_flags_aws_access_key():
    text = 'AWS_ACCESS_KEY_ID = "AKIAABCDEFGHIJKLMNOP"\n'
    failures = scan_for_secrets(text, "x.py")
    assert any(f.rule.startswith("secrets:aws_access_key") for f in failures)


def test_flags_vault_named_assignment():
    text = 'API_KEY = "this-is-a-long-secret-value-zzz"\n'
    failures = scan_for_secrets(text, "x.py")
    assert any("vault_naming" in f.rule for f in failures)


def test_skips_vault_read_call_value():
    text = 'API_KEY = vault.read("api_key")\n'
    failures = scan_for_secrets(text, "x.py")
    assert all("vault_naming" not in f.rule for f in failures)


def test_skips_environ_lookup():
    text = 'API_KEY = os.environ["API_KEY"]\n'
    failures = scan_for_secrets(text, "x.py")
    assert all("vault_naming" not in f.rule for f in failures)


def test_passes_clean_text():
    text = "from typing import Any\n\ndef tool(x: Any) -> Any:\n    return x\n"
    failures = scan_for_secrets(text, "x.py")
    assert failures == []


def test_detect_secrets_disabled_by_default():
    # Long random string that doesn't match any provider regex; should
    # not be flagged with the curated set alone.
    text = 'DATA = "abcdefghijklmnopqrstuvwxyz0123456789"\n'
    failures = scan_for_secrets(text, "x.py", detect_secrets_enabled=False)
    assert failures == []
