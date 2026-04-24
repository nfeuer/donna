"""Boot wiring: memory_search tool registers iff memory.yaml + vec0 + vault are all up.

Mirrors `tests/integration/test_boot_vault_wiring.py` for the slice-13
memory surface.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from donna.cli_wiring import (
    _try_build_memory_store,
    _try_build_vault_client,
)
from donna.skills.tools import DEFAULT_TOOL_REGISTRY, register_default_tools


class _StubInvocationLogger:
    async def log(self, *_args: object, **_kwargs: object) -> str:
        return "stub-id"


class _StubDB:
    """Minimal stub that mimics Database.vec_available + connection."""

    def __init__(self, *, vec_available: bool, connection: object | None = None) -> None:
        self.vec_available = vec_available
        self.connection = connection or object()


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
        "embedding:\n"
        "  provider: minilm-l6-v2\n"
        "  version_tag: minilm-l6-v2@2024-05\n"
        "  dim: 384\n"
        "  max_tokens: 256\n"
        "  chunk_overlap: 32\n"
        "retrieval:\n"
        "  default_k: 5\n"
        "  min_score: 0.0\n"
        "  max_k: 10\n"
        "sources:\n"
        "  vault:\n"
        "    enabled: true\n"
        "    chunker: markdown_heading\n"
        "    ignore_globs: []\n"
    )


@pytest.mark.integration
def test_memory_store_unavailable_when_vec_missing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    _write_memory_yaml(config_dir, vault_root)

    client = _try_build_vault_client(config_dir)
    assert client is not None
    db = _StubDB(vec_available=False)
    store, handles = asyncio.run(
        _try_build_memory_store(
            config_dir, db, "nick", _StubInvocationLogger(), client,
        )
    )
    assert store is None
    assert handles is None


@pytest.mark.integration
def test_memory_search_absent_when_store_none() -> None:
    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY)
    assert "memory_search" not in DEFAULT_TOOL_REGISTRY.list_tool_names()


@pytest.mark.integration
def test_memory_store_unavailable_when_memory_yaml_missing(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db = _StubDB(vec_available=True)
    store, handles = asyncio.run(
        _try_build_memory_store(
            config_dir, db, "nick", _StubInvocationLogger(), None,
        )
    )
    assert store is None
    assert handles is None
