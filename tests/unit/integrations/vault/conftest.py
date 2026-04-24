"""Shared fixtures for vault unit tests (slice 12)."""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import (
    MemoryConfig,
    VaultConfig,
    VaultSafetyConfig,
)
from donna.integrations.git_repo import GitRepo
from donna.integrations.vault import VaultClient, VaultWriter


@pytest.fixture
def memory_config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        vault=VaultConfig(
            root=str(tmp_path),
            git_author_name="Donna Test",
            git_author_email="donna-test@example.com",
            sync_method="manual",
        ),
        safety=VaultSafetyConfig(
            max_note_bytes=5_000,
            path_allowlist=["Inbox", "Meetings"],
            sensitive_frontmatter_key="donna_sensitive",
        ),
    )


@pytest.fixture
async def writer(memory_config: MemoryConfig) -> VaultWriter:
    git = GitRepo(
        root=Path(memory_config.vault.root),
        author_name=memory_config.vault.git_author_name,
        author_email=memory_config.vault.git_author_email,
    )
    client = VaultClient(memory_config)
    w = VaultWriter(memory_config, git, client=client)
    await w.ensure_ready()
    return w
