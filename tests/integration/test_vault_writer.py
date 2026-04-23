"""End-to-end: vault_write -> file on disk -> git commit -> read -> undo.

Exercises VaultClient + VaultWriter + GitRepo against a real git binary
and a tmp_path vault root.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import MemoryConfig, VaultConfig, VaultSafetyConfig
from donna.integrations.git_repo import GitRepo
from donna.integrations.vault import VaultClient, VaultWriter


@pytest.fixture
def cfg(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        vault=VaultConfig(
            root=str(tmp_path),
            git_author_name="Donna IT",
            git_author_email="donna-it@example.com",
            sync_method="manual",
        ),
        safety=VaultSafetyConfig(
            max_note_bytes=50_000,
            path_allowlist=["Inbox", "Meetings", "People"],
        ),
    )


@pytest.fixture
async def writer(cfg: MemoryConfig) -> VaultWriter:
    git = GitRepo(
        root=Path(cfg.vault.root),
        author_name=cfg.vault.git_author_name,
        author_email=cfg.vault.git_author_email,
    )
    client = VaultClient(cfg)
    w = VaultWriter(cfg, git, client=client)
    await w.ensure_ready()
    return w


@pytest.mark.asyncio
async def test_write_produces_file_and_commit(writer: VaultWriter) -> None:
    sha = await writer.write("Inbox/hello.md", "# Hello\n")
    assert len(sha) == 40

    path = Path(writer.root) / "Inbox" / "hello.md"
    assert path.exists()
    assert "Hello" in path.read_text()

    log = await writer.git.log(limit=3)
    # Most recent commit is our write.
    assert any("write Inbox/hello.md" in entry.message for entry in log)


@pytest.mark.asyncio
async def test_read_returns_matching_mtime(writer: VaultWriter) -> None:
    await writer.write("Inbox/mtime.md", "# body\n")
    note = await writer._client.read("Inbox/mtime.md")

    disk = Path(writer.root) / "Inbox" / "mtime.md"
    assert note.mtime == disk.stat().st_mtime
    assert note.size == disk.stat().st_size


@pytest.mark.asyncio
async def test_undo_last_removes_the_file(writer: VaultWriter) -> None:
    await writer.write("Inbox/undo.md", "# will vanish\n")
    path = Path(writer.root) / "Inbox" / "undo.md"
    assert path.exists()

    reverts = await writer.undo_last(1)
    assert len(reverts) == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_list_returns_relative_paths(writer: VaultWriter) -> None:
    await writer.write("Inbox/a.md", "# a\n")
    await writer.write("Meetings/b.md", "# b\n")

    all_paths = await writer._client.list()
    assert "Inbox/a.md" in all_paths
    assert "Meetings/b.md" in all_paths

    just_inbox = await writer._client.list(folder="Inbox")
    assert just_inbox == ["Inbox/a.md"]


@pytest.mark.asyncio
async def test_move_and_delete(writer: VaultWriter) -> None:
    await writer.write("Inbox/src.md", "# src\n")
    src_path = Path(writer.root) / "Inbox" / "src.md"
    dst_path = Path(writer.root) / "Meetings" / "dst.md"

    await writer.move("Inbox/src.md", "Meetings/dst.md")
    assert not src_path.exists()
    assert dst_path.exists()

    await writer.delete("Meetings/dst.md")
    assert not dst_path.exists()


@pytest.mark.asyncio
async def test_extract_links(writer: VaultWriter) -> None:
    body = "See [[Project Alpha]] and [[People/Alice|Alice]] and [[Meetings/2026-04-23#topic]].\n"
    await writer.write("Inbox/links.md", body)
    targets = await writer._client.extract_links("Inbox/links.md")
    assert "Project Alpha" in targets
    assert "People/Alice" in targets
    assert "Meetings/2026-04-23" in targets
