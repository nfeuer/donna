"""email_read — structured-kwargs wrapper around GmailClient.search_emails.

Distinct from ``gmail_search``: this tool exposes structured filter kwargs
(``from_sender``, ``subject_contains``, ``is_unread``, ``since``, ``until``)
and composes the Gmail query string internally. LLMs constructing tool calls
don't have to know Gmail's query grammar.

Read-only by construction: never imports or references draft/send methods.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import structlog

logger = structlog.get_logger()

MAX_RESULTS_CEILING = 100


class EmailReadError(Exception):
    """Raised when an email_read invocation fails."""


async def email_read(
    *,
    client: Any,
    from_sender: str | None = None,
    subject_contains: str | None = None,
    is_unread: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    max_results: int = 20,
) -> dict:
    """List messages matching the given structured filters.

    At least one filter must be provided. ``since`` / ``until`` are ISO-8601
    dates (``YYYY-MM-DD``). Returns lightweight summaries, never bodies.
    """
    query = _build_query(
        from_sender=from_sender,
        subject_contains=subject_contains,
        is_unread=is_unread,
        since=since,
        until=until,
    )
    if not query:
        raise EmailReadError(
            "email_read requires at least one filter "
            "(from_sender, subject_contains, is_unread, since, until)"
        )

    clamped = min(int(max_results), MAX_RESULTS_CEILING)
    try:
        messages = await client.search_emails(query=query, max_results=clamped)
    except Exception as exc:
        logger.warning("email_read_failed", query=query, error=str(exc))
        raise EmailReadError(f"search_emails: {exc}") from exc

    out = []
    for m in messages:
        out.append({
            "id": m.id,
            "sender": m.sender,
            "subject": m.subject,
            "snippet": m.snippet,
            "internal_date": m.date.isoformat() if m.date is not None else None,
        })
    return {"ok": True, "query": query, "messages": out}


def _build_query(
    *,
    from_sender: str | None,
    subject_contains: str | None,
    is_unread: bool | None,
    since: str | None,
    until: str | None,
) -> str:
    parts: list[str] = []
    if from_sender:
        parts.append(f"from:{from_sender}")
    if subject_contains:
        parts.append(f'subject:"{subject_contains}"')
    if is_unread is True:
        parts.append("is:unread")
    elif is_unread is False:
        parts.append("is:read")
    if since:
        parts.append(f"after:{_coerce_date(since, field='since')}")
    if until:
        parts.append(f"before:{_coerce_date(until, field='until')}")
    return " ".join(parts)


def _coerce_date(value: str, *, field: str) -> str:
    """Validate ISO ``YYYY-MM-DD`` and return Gmail's ``YYYY/MM/DD`` form."""
    try:
        d = date.fromisoformat(value)
    except ValueError as exc:
        raise EmailReadError(f"invalid {field} date: {exc}") from exc
    return d.strftime("%Y/%m/%d")
