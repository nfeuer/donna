"""gmail_search — thin read-only wrapper around GmailClient.search_emails.

Registered into DEFAULT_TOOL_REGISTRY via donna.skills.tools.register_default_tools
(wired in Task 7). Only registered when a GmailClient is available at boot.

Read-only by construction: this wrapper only ever reads from the
underlying GmailClient. It does not import or reference draft/send methods.
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

MAX_RESULTS_CEILING = 100


class GmailToolError(Exception):
    """Raised when a Gmail tool cannot complete its call."""


async def gmail_search(
    *,
    client: Any,
    query: str,
    max_results: int = 20,
) -> dict:
    """Search Gmail. Returns lightweight summaries, never bodies."""
    if not query or not query.strip():
        raise GmailToolError("query must be non-empty")
    clamped = min(int(max_results), MAX_RESULTS_CEILING)
    try:
        messages = await client.search_emails(query=query, max_results=clamped)
    except Exception as exc:
        logger.warning("gmail_search_failed", query=query, error=str(exc))
        raise GmailToolError(f"search: {exc}") from exc

    out = []
    for m in messages:
        out.append({
            "id": m.id,
            "sender": m.sender,
            "subject": m.subject,
            "snippet": m.snippet,
            "internal_date": m.date.isoformat() if m.date is not None else None,
        })
    return {"ok": True, "messages": out}
