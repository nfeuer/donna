"""Tiny slug helper for meeting-note filenames.

Used by :mod:`donna.capabilities.meeting_note_skill` to turn an event
title into the ``<date>-<slug>.md`` path segment. Intentionally minimal
— one caller, no Unicode normalisation, no stopword stripping.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 60) -> str:
    """Lowercase ASCII slug. Collapses runs of non-alphanumerics to ``-``.

    Returns ``"untitled"`` when the input slugifies to an empty string.
    """
    lowered = text.lower()
    slug = _NON_ALNUM.sub("-", lowered).strip("-")
    if not slug:
        return "untitled"
    return slug[:max_len].rstrip("-") or "untitled"
