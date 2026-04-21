"""Integration test: confirm Gmail tools register when email config present."""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.cli_wiring import _try_build_gmail_client
from donna.skills.tools import DEFAULT_TOOL_REGISTRY, register_default_tools


def test_gmail_tools_register_when_client_present(tmp_path: Path) -> None:
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
    client = _try_build_gmail_client(tmp_path)
    assert client is not None

    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY, gmail_client=client)
    names = DEFAULT_TOOL_REGISTRY.list_tool_names()
    assert "gmail_search" in names
    assert "gmail_get_message" in names


def test_gmail_tools_absent_when_client_missing(tmp_path: Path) -> None:
    client = _try_build_gmail_client(tmp_path)  # no email.yaml
    assert client is None

    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY, gmail_client=client)
    names = DEFAULT_TOOL_REGISTRY.list_tool_names()
    assert "gmail_search" not in names
    assert "gmail_get_message" not in names
