"""vault_link — extract Obsidian-style ``[[wikilinks]]`` from a note (slice 12).

Minimal resolution: aliases (``[[target|alias]]``) and sub-headings
(``[[target#heading]]``) are stripped; the bare target is returned. Full
link-graph traversal lands in a later slice.
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class VaultLinkError(Exception):
    """Raised when ``vault_link`` fails."""


async def vault_link(*, client: Any, path: str) -> dict[str, Any]:
    """Return the list of ``[[wikilink]]`` targets in the note at ``path``."""
    try:
        targets = await client.extract_links(path)
    except Exception as exc:
        logger.warning("vault_link_failed", path=path, error=str(exc))
        raise VaultLinkError(f"vault_link: {exc}") from exc
    return {"ok": True, "path": path, "links": targets}
