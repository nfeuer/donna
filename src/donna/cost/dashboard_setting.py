"""Read-only resolution layer for dashboard runtime overrides.

Resolution order (per docs/superpowers/specs/manual-escalation.md §6.3):
``dashboard_setting`` row → caller-supplied YAML default. The dashboard
write path and UI ship in slice 23; slice 17 only needs the read side
so other slices can flip toggles by upserting rows directly during
testing.
"""

from __future__ import annotations

from typing import TypeVar

import structlog

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
