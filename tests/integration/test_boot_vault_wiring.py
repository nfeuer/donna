"""Boot wiring: vault tools register iff memory.yaml + vault client are present.

Mirrors tests/integration/test_boot_gmail_wiring.py line-for-line for
the vault surface added in slice 12.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from donna.cli_wiring import (
    _try_build_vault_client,
    _try_build_vault_writer,
)
from donna.skills.tools import DEFAULT_TOOL_REGISTRY, register_default_tools

VAULT_TOOLS = {
    "vault_read",
    "vault_write",
    "vault_list",
    "vault_link",
    "vault_undo_last",
}


def _write_memory_yaml(config_dir: Path, vault_root: Path) -> None:
    (config_dir / "memory.yaml").write_text(
        "vault:\n"
        f"  root: {vault_root}\n"
        "  git_author_name: Donna\n"
        "  git_author_email: donna@example.com\n"
        "  sync_method: manual\n"
        "safety:\n"
        "  max_note_bytes: 50000\n"
        "  path_allowlist: [Inbox, Meetings]\n"
    )


def test_vault_tools_register_when_client_and_writer_present(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    vault_root = tmp_path / "vault"
    # Parent directory must exist (the client builder checks it); the
    # vault root itself is created on-demand by ensure_ready().
    vault_root.parent.mkdir(exist_ok=True)
    _write_memory_yaml(config_dir, vault_root)

    client = _try_build_vault_client(config_dir)
    assert client is not None

    writer = asyncio.run(_try_build_vault_writer(config_dir, client))
    assert writer is not None

    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(
        DEFAULT_TOOL_REGISTRY,
        vault_client=client,
        vault_writer=writer,
    )
    names = set(DEFAULT_TOOL_REGISTRY.list_tool_names())
    assert VAULT_TOOLS.issubset(names)


def test_vault_tools_absent_when_memory_yaml_missing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    client = _try_build_vault_client(config_dir)
    assert client is None

    writer = asyncio.run(_try_build_vault_writer(config_dir, client))
    assert writer is None

    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(
        DEFAULT_TOOL_REGISTRY,
        vault_client=client,
        vault_writer=writer,
    )
    names = set(DEFAULT_TOOL_REGISTRY.list_tool_names())
    assert names.isdisjoint(VAULT_TOOLS)
