"""Unit tests for _try_build_gmail_client helper."""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.cli_wiring import _try_build_gmail_client


def test_returns_none_when_email_yaml_missing(tmp_path: Path) -> None:
    result = _try_build_gmail_client(tmp_path)
    assert result is None


def test_returns_none_when_creds_file_missing(tmp_path: Path) -> None:
    (tmp_path / "email.yaml").write_text(
        "credentials:\n"
        f"  token_path: {tmp_path}/nonexistent_token.json\n"
        f"  client_secrets_path: {tmp_path}/nonexistent_secrets.json\n"
        "  scopes: ['https://www.googleapis.com/auth/gmail.readonly']\n"
    )
    result = _try_build_gmail_client(tmp_path)
    assert result is None


def test_returns_client_when_config_present(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    secrets = tmp_path / "secrets.json"
    token.write_text("{}")
    secrets.write_text("{}")
    (tmp_path / "email.yaml").write_text(
        "credentials:\n"
        f"  token_path: {token}\n"
        f"  client_secrets_path: {secrets}\n"
        "  scopes: ['https://www.googleapis.com/auth/gmail.readonly']\n"
    )
    result = _try_build_gmail_client(tmp_path)
    assert result is not None
    assert type(result).__name__ == "GmailClient"


def test_returns_none_when_construction_raises(tmp_path: Path) -> None:
    (tmp_path / "email.yaml").write_text("email: {broken")  # malformed YAML
    result = _try_build_gmail_client(tmp_path)
    assert result is None
