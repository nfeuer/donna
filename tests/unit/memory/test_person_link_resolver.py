"""Slice 15 — ``resolve_person_link`` unit tests.

Exists → namespaced wikilink. Missing → bare wikilink. Never creates a
stub note on disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import MemoryConfig, VaultConfig, VaultSafetyConfig
from donna.integrations.vault import VaultClient
from donna.memory.linking import resolve_person_link


@pytest.fixture
def cfg(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        vault=VaultConfig(
            root=str(tmp_path),
            git_author_name="Donna",
            git_author_email="donna@example.com",
            sync_method="manual",
        ),
        safety=VaultSafetyConfig(
            max_note_bytes=10_000,
            path_allowlist=["People", "Inbox", "Meetings"],
        ),
    )


@pytest.fixture
def client(cfg: MemoryConfig, tmp_path: Path) -> VaultClient:
    (tmp_path / "People").mkdir()
    (tmp_path / "People" / "Alice.md").write_text("# Alice\n", encoding="utf-8")
    return VaultClient(cfg)


@pytest.mark.asyncio
async def test_resolves_to_namespaced_when_file_exists(
    client: VaultClient,
) -> None:
    link = await resolve_person_link("Alice", client)
    assert link == "[[People/Alice]]"


@pytest.mark.asyncio
async def test_falls_back_to_bare_wikilink_when_missing(
    client: VaultClient, tmp_path: Path
) -> None:
    link = await resolve_person_link("Bob", client)
    assert link == "[[Bob]]"
    # No stub created — key guarantee of the resolver.
    assert not (tmp_path / "People" / "Bob.md").exists()
