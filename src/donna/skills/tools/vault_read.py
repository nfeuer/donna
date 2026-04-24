"""vault_read — read a markdown note from the Donna vault (slice 12).

Returns the body, parsed YAML frontmatter, mtime, and size so callers
can round-trip optimistic concurrency via ``expected_mtime`` on a
subsequent ``vault_write``.
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class VaultToolError(Exception):
    """Raised when a vault tool invocation fails."""


async def vault_read(*, client: Any, path: str) -> dict[str, Any]:
    """Read a note at ``path`` (forward-slash relative to vault root)."""
    try:
        note = await client.read(path)
    except Exception as exc:
        logger.warning("vault_read_failed", path=path, error=str(exc))
        raise VaultToolError(f"vault_read: {exc}") from exc
    return {
        "ok": True,
        "path": note.path,
        "content": note.content,
        "frontmatter": note.frontmatter,
        "mtime": note.mtime,
        "size": note.size,
    }
