"""vault_write — write or overwrite a markdown note (slice 12).

Delegates every safety check to :class:`donna.integrations.vault.VaultWriter`.
Supports optimistic concurrency via ``expected_mtime``: pass the mtime
returned by a prior ``vault_read`` and the write is rejected if the file
has changed in between.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.integrations.vault import VaultWriteError

logger = structlog.get_logger()


class VaultWriteToolError(Exception):
    """Raised when a ``vault_write`` invocation fails."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


async def vault_write(
    *,
    client: Any,
    path: str,
    content: str,
    expected_mtime: float | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Create or overwrite the note at ``path``; return the commit SHA."""
    try:
        sha = await client.write(
            path=path,
            content=content,
            expected_mtime=expected_mtime,
            message=message,
        )
    except VaultWriteError as exc:
        logger.info("vault_write_rejected", path=path, reason=exc.reason)
        raise VaultWriteToolError(str(exc), reason=exc.reason) from exc
    except Exception as exc:
        logger.warning("vault_write_failed", path=path, error=str(exc))
        raise VaultWriteToolError(f"vault_write: {exc}") from exc
    return {"ok": True, "path": path, "commit_sha": sha}
