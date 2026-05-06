"""Read-only resolution layer for dashboard runtime overrides.

Resolution order (per docs/superpowers/specs/manual-escalation.md §6.3):
``dashboard_setting`` row → caller-supplied YAML default. Slice 23
adds the write path; resolution stays through this single class so
the gate's read sites do not change shape.

When slice 23's canonical key namespace was unified, two legacy keys
from earlier slices kept their old names in production rows
(``modes.claude_code.enabled`` and ``budget_extension.enabled``). The
resolver consults the canonical key first and falls back to legacy
aliases registered in :mod:`donna.cost.dashboard_settings_catalog`.
"""

from __future__ import annotations

from typing import TypeVar

import structlog

from donna.cost.dashboard_settings_catalog import SETTINGS_BY_KEY
from donna.cost.escalation_repository import EscalationRepository

logger = structlog.get_logger()

T = TypeVar("T")


class DashboardSettingResolver:
    """Resolves a settings key against the ``dashboard_setting`` table."""

    def __init__(self, repository: EscalationRepository) -> None:
        self._repo = repository

    async def get(self, key: str, default: T) -> T:
        """Return the stored override for ``key`` or ``default``.

        The stored JSON value is returned as-is. Callers are expected to
        know the key's type and pass a default of that type; on type
        mismatch we fall back to the default and log a warning.
        """
        try:
            stored = await self._repo.get_dashboard_setting(key)
            if stored is None:
                # Slice 23 — accept legacy keys written before the
                # namespace unification.
                spec = SETTINGS_BY_KEY.get(key)
                if spec is not None:
                    for alias in spec.legacy_aliases:
                        legacy = await self._repo.get_dashboard_setting(alias)
                        if legacy is not None:
                            stored = legacy
                            break
        except Exception:
            logger.exception("dashboard_setting_lookup_failed", key=key)
            return default
        if stored is None:
            return default
        if not isinstance(stored, type(default)) and default is not None:
            logger.warning(
                "dashboard_setting_type_mismatch",
                key=key,
                stored_type=type(stored).__name__,
                default_type=type(default).__name__,
            )
            return default
        # Type narrowed by the isinstance check above; mypy can't see it
        # through the generic T parameter so we cast.
        from typing import cast as _cast

        return _cast(T, stored)
