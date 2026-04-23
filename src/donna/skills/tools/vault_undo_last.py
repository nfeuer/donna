"""vault_undo_last — revert the last N vault commits (slice 12).

Uses ``git revert`` (not ``git reset``) so the audit trail is preserved.
Default ``n=1`` undoes the most recent write.
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class VaultUndoError(Exception):
    """Raised when ``vault_undo_last`` fails."""


async def vault_undo_last(*, client: Any, n: int = 1) -> dict[str, Any]:
    """Revert the last ``n`` commits; return the new revert SHAs."""
    try:
        shas = await client.undo_last(n=n)
    except Exception as exc:
        logger.warning("vault_undo_last_failed", n=n, error=str(exc))
        raise VaultUndoError(f"vault_undo_last: {exc}") from exc
    return {"ok": True, "n": n, "revert_shas": shas}
