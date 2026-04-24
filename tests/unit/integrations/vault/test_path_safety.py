"""Path-safety envelope enforced by VaultWriter (slice 12)."""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.integrations.vault import VaultWriteError, VaultWriter


@pytest.mark.asyncio
async def test_rejects_parent_traversal(writer: VaultWriter) -> None:
    with pytest.raises(VaultWriteError) as exc:
        await writer.write("Inbox/../escape.md", "# no")
    assert exc.value.reason == "path_escape"


@pytest.mark.asyncio
async def test_rejects_absolute_path(writer: VaultWriter) -> None:
    with pytest.raises(VaultWriteError) as exc:
        await writer.write("/etc/passwd.md", "# no")
    assert exc.value.reason == "path_escape"


@pytest.mark.asyncio
async def test_rejects_non_markdown_extension(writer: VaultWriter) -> None:
    with pytest.raises(VaultWriteError) as exc:
        await writer.write("Inbox/foo.txt", "hello")
    assert exc.value.reason == "not_markdown"


@pytest.mark.asyncio
async def test_rejects_outside_allowlist(writer: VaultWriter) -> None:
    with pytest.raises(VaultWriteError) as exc:
        await writer.write("Secrets/keys.md", "# no")
    assert exc.value.reason == "outside_allowlist"


@pytest.mark.asyncio
async def test_rejects_oversize_payload(writer: VaultWriter) -> None:
    huge = "x" * 10_000  # fixture cap is 5_000 bytes
    with pytest.raises(VaultWriteError) as exc:
        await writer.write("Inbox/big.md", huge)
    assert exc.value.reason == "too_large"


@pytest.mark.asyncio
async def test_rejects_symlink_escape(writer: VaultWriter, tmp_path: Path) -> None:
    # Create a symlink inside the vault pointing at /tmp (outside vault root).
    vault_root = writer.root
    outside = tmp_path.parent / "outside_vault_symlink_target"
    outside.mkdir(exist_ok=True)
    link = vault_root / "Inbox" / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(VaultWriteError) as exc:
        await writer.write("Inbox/escape/leak.md", "# no")
    assert exc.value.reason == "path_escape"
