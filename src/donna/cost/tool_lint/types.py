"""Shared lint types (separated to avoid circular imports)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LintFailure:
    """One lint violation.

    ``rule`` matches the §10.5 row name (``secrets``, ``anthropic_import``,
    ``import_io``, ``allowlist``, ``metadata``, ``inert_test``,
    ``syntax``, ``requires_rebuild_warning``).
    """

    rule: str
    path: str
    line: int | None
    message: str
