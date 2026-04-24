"""vault_list — enumerate markdown notes under a vault folder (slice 12)."""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class VaultListError(Exception):
    """Raised when ``vault_list`` fails."""


async def vault_list(
    *,
    client: Any,
    folder: str = "",
    recursive: bool = True,
) -> dict[str, Any]:
    """List notes under ``folder`` (default: vault root).

    Returns forward-slash relative paths. Patterns in
    ``memory.yaml:vault.ignore_globs`` are filtered out.
    """
    try:
        paths = await client.list(folder=folder, recursive=recursive)
    except Exception as exc:
        logger.warning("vault_list_failed", folder=folder, error=str(exc))
        raise VaultListError(f"vault_list: {exc}") from exc
    return {"ok": True, "folder": folder, "paths": paths}
