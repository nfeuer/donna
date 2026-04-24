"""Optimistic concurrency: stale expected_mtime must raise conflict (slice 12)."""
from __future__ import annotations

import pytest

from donna.integrations.vault import VaultWriteError, VaultWriter


@pytest.mark.asyncio
async def test_write_with_fresh_mtime_succeeds(writer: VaultWriter) -> None:
    await writer.write("Inbox/note.md", "# first")
    _, size_before = await writer._client.stat("Inbox/note.md")
    note = await writer._client.read("Inbox/note.md")
    sha = await writer.write(
        "Inbox/note.md", "# second", expected_mtime=note.mtime
    )
    assert sha
    _, size_after = await writer._client.stat("Inbox/note.md")
    assert size_after != size_before


@pytest.mark.asyncio
async def test_write_with_stale_mtime_conflicts(writer: VaultWriter) -> None:
    await writer.write("Inbox/note.md", "# first")
    # A clearly stale mtime (epoch).
    with pytest.raises(VaultWriteError) as exc:
        await writer.write(
            "Inbox/note.md", "# second", expected_mtime=1.0
        )
    assert exc.value.reason == "conflict"


@pytest.mark.asyncio
async def test_write_missing_file_with_any_expected_mtime_is_create(
    writer: VaultWriter,
) -> None:
    # If the file does not yet exist, expected_mtime is simply ignored
    # (nothing to conflict with). This keeps the API ergonomic for
    # "create-or-update" callers.
    sha = await writer.write(
        "Inbox/fresh.md", "# brand new", expected_mtime=1.0
    )
    assert sha
