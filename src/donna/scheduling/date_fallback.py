"""LLM-free temporal extraction so dated tasks still route when parsing degrades.

Handles the common phrasings ("tomorrow", a named weekday, "next week",
"end of month") using stdlib + dateparser. Intentionally conservative: when in
doubt it returns kind="none" rather than guessing. This is a *fallback*, not the
primary parser — see input_parser.parse for where it is invoked.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime, timedelta

import dateparser

from donna.scheduling.time_intent import TimeIntent


def _month_end(now: datetime) -> datetime:
    last = calendar.monthrange(now.year, now.month)[1]
    return now.replace(day=last, hour=23, minute=59, second=0, microsecond=0)


def fallback_time_intent(text: str, now: datetime | None = None) -> TimeIntent:
    """Best-effort TimeIntent from raw text, no LLM. Returns kind='none' if unsure."""
    now = now or datetime.now(tz=UTC)
    lowered = text.lower()

    # Window phrasings take precedence over a bare date inside them.
    if "next week" in lowered:
        days_to_monday = (7 - now.weekday()) % 7 or 7
        start = (now + timedelta(days=days_to_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return TimeIntent(
            kind="window", earliest=start, latest=start + timedelta(days=6), strictness="soft"
        )

    if re.search(r"end of (the )?month", lowered):
        return TimeIntent(kind="window", earliest=now, latest=_month_end(now), strictness="soft")

    # Extract common date phrases from text to try dateparser on keywords alone.
    # Dateparser works better with just the date part, not embedded in a sentence.
    date_keywords = [
        "tomorrow",
        "today",
        "tonight",
        "yesterday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    found_keyword = None
    for keyword in date_keywords:
        if keyword in lowered:
            found_keyword = keyword
            break

    # Try parsing the found keyword or the full text
    to_parse = found_keyword if found_keyword else text
    parsed = dateparser.parse(
        to_parse,
        settings={
            "RELATIVE_BASE": now.replace(tzinfo=None),
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is not None:
        due = parsed.replace(tzinfo=UTC)
        if due.hour == 0 and due.minute == 0:
            due = due.replace(hour=12)  # noon default for date-only phrases
        return TimeIntent(kind="exact", due_at=due, strictness="soft")

    return TimeIntent(kind="none")
