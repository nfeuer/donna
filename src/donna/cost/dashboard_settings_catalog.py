"""Canonical catalog for slice 23 dashboard runtime overrides.

Realizes docs/superpowers/specs/manual-escalation.md §6.3(a). This is
the single source of truth for which YAML keys are dashboard-mutable,
the type of each value, and the per-task-type override grid contract.

Every key in :data:`SETTINGS` follows the dot-path of the YAML structure
under ``manual_escalation.*`` so the resolver and the dashboard share
one namespace. Slice 17 / 20 / 21 used a couple of legacy keys
(``modes.claude_code.enabled`` and ``budget_extension.enabled``);
:func:`legacy_aliases_for` returns the old name(s) so the resolver can
fall through during the slice 23 migration without breaking running
deployments. New writes always go to the canonical key.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any, Literal

from donna.config import ManualEscalationConfig

# ---------------------------------------------------------------------------
# Per-task-type override grid
# ---------------------------------------------------------------------------

TaskTypeOverride = Literal[
    "auto",          # default — use config + global toggles unchanged
    "force_api",     # only api_extended button (manual handoff hidden)
    "force_manual",  # only manual handoff button (api_extended hidden)
    "disabled",      # neither — task always falls through to pause / cancel
]
"""Allowed values for the per-task-type override grid (spec §6.3(a))."""

TASK_TYPE_OVERRIDE_VALUES: tuple[TaskTypeOverride, ...] = (
    "auto",
    "force_api",
    "force_manual",
    "disabled",
)

TASK_TYPE_OVERRIDE_DEFAULT: TaskTypeOverride = "auto"


def task_type_override_key(task_type: str) -> str:
    """Build the dashboard_setting key for a per-task-type override.

    Mirrors the YAML namespace so the resolver / write API can treat
    these like any other key without a special case.
    """
    return f"manual_escalation.task_types.{task_type}.override"


# ---------------------------------------------------------------------------
# Top-level toggle catalog
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DashboardSettingSpec:
    """Schema entry for one dashboard-mutable setting."""

    key: str
    """Canonical dashboard_setting key (dot-path matching YAML)."""

    value_type: type
    """Python type the value coerces to. ``bool`` and ``float`` accepted
    here; integer/string sliders use ``float`` / ``str``."""

    description: str
    """One-line human description rendered next to the control in the UI."""

    legacy_aliases: tuple[str, ...] = ()
    """Older key names callers may have stored before slice 23. The
    resolver checks the canonical key first, then each alias in order."""


# Order of definition is the order the dashboard renders them.
SETTINGS: tuple[DashboardSettingSpec, ...] = (
    DashboardSettingSpec(
        key="manual_escalation.enabled",
        value_type=bool,
        description="Master kill switch for over-budget escalations.",
    ),
    DashboardSettingSpec(
        key="manual_escalation.modes.chat.enabled",
        value_type=bool,
        description="Allow text-only manual handoff (Claude.ai paste-back).",
    ),
    DashboardSettingSpec(
        key="manual_escalation.modes.claude_code.enabled",
        value_type=bool,
        description="Allow file-artifact manual handoff (Claude Code worktree).",
        # Slice 17/21 stored this without the ``manual_escalation.`` prefix.
        legacy_aliases=("modes.claude_code.enabled",),
    ),
    DashboardSettingSpec(
        key="manual_escalation.budget_extension.enabled",
        value_type=bool,
        description="Allow user-approved one-shot daily budget extensions.",
        # Slice 18 stored this without the ``manual_escalation.`` prefix.
        legacy_aliases=("budget_extension.enabled",),
    ),
    DashboardSettingSpec(
        key="manual_escalation.budget_extension.max_daily_extension_usd",
        value_type=float,
        description=(
            "Per-day cap on cumulative extensions (USD). "
            "Capped at hard_monthly_ceiling_usd / days_left_in_month."
        ),
    ),
)
"""Catalog of dashboard-mutable settings, in display order."""


SETTINGS_BY_KEY: dict[str, DashboardSettingSpec] = {s.key: s for s in SETTINGS}


def is_known_key(key: str) -> bool:
    """Return True if ``key`` is in the catalog or a per-task-type override."""
    if key in SETTINGS_BY_KEY:
        return True
    return key.startswith("manual_escalation.task_types.") and key.endswith(
        ".override"
    )


def coerce_value(spec: DashboardSettingSpec, raw: Any) -> Any:
    """Coerce a JSON-deserialised value to the catalog's declared type.

    Raises :class:`ValueError` when the value is unusable; callers turn
    that into a 422 response so the user sees an actionable message.
    """
    if spec.value_type is bool:
        if isinstance(raw, bool):
            return raw
        raise ValueError(f"{spec.key} requires a boolean")
    if spec.value_type is float:
        if isinstance(raw, bool):
            # ``bool`` is a subclass of ``int``; reject it explicitly so
            # ``True/False`` cannot stand in for a slider value.
            raise ValueError(f"{spec.key} requires a number, not bool")
        if isinstance(raw, (int, float)):
            return float(raw)
        raise ValueError(f"{spec.key} requires a number")
    if spec.value_type is str:
        if isinstance(raw, str):
            return raw
        raise ValueError(f"{spec.key} requires a string")
    raise ValueError(f"{spec.key} has unsupported type {spec.value_type!r}")


def coerce_task_type_override(raw: Any) -> TaskTypeOverride:
    """Validate a per-task-type override value."""
    if raw not in TASK_TYPE_OVERRIDE_VALUES:
        raise ValueError(
            f"override must be one of {TASK_TYPE_OVERRIDE_VALUES}, got {raw!r}"
        )
    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# YAML default lookup
# ---------------------------------------------------------------------------


def yaml_default_for(
    key: str, config: ManualEscalationConfig
) -> Any:
    """Return the YAML bootstrap default for ``key``.

    Mirrors the shape stored in ``config/manual_escalation.yaml``. Keeps
    the dashboard's "what is the YAML default?" column in sync with the
    actual config without re-parsing the file.

    Raises :class:`KeyError` if the key is not in :data:`SETTINGS`.
    """
    if key == "manual_escalation.enabled":
        return config.enabled
    if key == "manual_escalation.modes.chat.enabled":
        return config.modes.chat.enabled
    if key == "manual_escalation.modes.claude_code.enabled":
        return config.modes.claude_code.enabled
    if key == "manual_escalation.budget_extension.enabled":
        return config.budget_extension.enabled
    if key == "manual_escalation.budget_extension.max_daily_extension_usd":
        return config.budget_extension.max_daily_extension_usd
    raise KeyError(key)


# ---------------------------------------------------------------------------
# Slider cap helper (§6.3(a))
# ---------------------------------------------------------------------------


def days_left_in_month(today: date) -> int:
    """Return the number of full days remaining in ``today``'s month.

    Used by :func:`max_daily_extension_cap_usd` to derive the slider's
    upper bound so a single day's extension cannot, on its own, breach
    the YAML-only ``hard_monthly_ceiling_usd``.

    The current day counts as remaining (a user can spend through the
    end of today), so a value of 1 on the last day of the month means
    "today is the only remaining day".
    """
    if today.month == 12:
        end = today.replace(year=today.year + 1, month=1, day=1)
    else:
        end = today.replace(month=today.month + 1, day=1)
    return (end - today).days


def max_daily_extension_cap_usd(
    config: ManualEscalationConfig, today: date
) -> float:
    """Per-day ceiling for the slider, derived from the monthly cap.

    ``hard_monthly_ceiling_usd / days_left_in_month`` (spec §6.3(a)).
    The hard monthly ceiling is YAML-only on purpose so a compromised
    dashboard session cannot raise this — it can only set a number at
    or below this cap.
    """
    days = max(1, days_left_in_month(today))
    return float(config.budget_extension.hard_monthly_ceiling_usd) / float(days)
