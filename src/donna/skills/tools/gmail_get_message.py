"""gmail_get_message — thin read-only wrapper around GmailClient.get_message.

Registered into DEFAULT_TOOL_REGISTRY via donna.skills.tools.register_default_tools
(wired in Task 7). Only registered when a GmailClient is available at boot.

Returns plain-text body preferentially; HTML body only when no plain
alternative exists. Read-only by construction.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.skills.tools.gmail_search import GmailToolError

logger = structlog.get_logger()


async def gmail_get_message(
    *,
    client: Any,
    message_id: str,
) -> dict[str, Any]:
    if not message_id or not message_id.strip():
        raise GmailToolError("message_id must be non-empty")
    try:
        m = await client.get_message(message_id=message_id)
    except Exception as exc:
        logger.warning("gmail_get_message_failed", id=message_id, error=str(exc))
        raise GmailToolError(f"get_message: {exc}") from exc

    body_plain = getattr(m, "body_text", "") or ""
    body_html = getattr(m, "body_html", None)
    if body_plain:
        body_html = None  # prefer plain
    return {
        "ok": True,
        "sender": m.sender,
        "subject": m.subject,
        "body_plain": body_plain,
        "body_html": body_html,
        "internal_date": m.date.isoformat() if m.date is not None else None,
        "headers": {"To": ", ".join(getattr(m, "recipients", []) or [])},
    }
