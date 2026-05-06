"""Slice 23 — write-side unit tests for ``dashboard_setting``.

Covers:
- :meth:`EscalationRepository.set_dashboard_setting_with_lock` happy path
- ``expected_updated_at=None`` behaviour (insert-only)
- 409 on stale lock token (spec §10.7 row 1)
- :func:`yaml_default_for` / :func:`max_daily_extension_cap_usd` shape
- :func:`coerce_value` / :func:`coerce_task_type_override` validation
- Catalog includes every key the dashboard exposes (drift guard)

Realizes docs/superpowers/specs/manual-escalation.md §6.3(a) / §10.7 row 1.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import aiosqlite
import pytest

from donna.config import (
    BudgetExtensionConfig,
    ManualEscalationConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
)
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.dashboard_settings_catalog import (
    SETTINGS,
    TASK_TYPE_OVERRIDE_VALUES,
    coerce_task_type_override,
    coerce_value,
    days_left_in_month,
    is_known_key,
    max_daily_extension_cap_usd,
    task_type_override_key,
    yaml_default_for,
)
from donna.cost.escalation_repository import EscalationRepository

_SCHEMA = """
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "ds.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


@pytest.fixture
def yaml_config() -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(),
        budget_extension=BudgetExtensionConfig(
            enabled=True,
            max_daily_extension_usd=10.0,
            hard_monthly_ceiling_usd=150.0,
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=5.0,
        ),
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class TestCatalog:
    """Drift guards on the slice 23 settings catalog."""

    def test_every_setting_has_yaml_default(
        self, yaml_config: ManualEscalationConfig
    ) -> None:
        for spec in SETTINGS:
            # Should not raise — every catalog entry must map to a YAML value.
            yaml_default_for(spec.key, yaml_config)

    def test_known_keys_include_catalog_and_overrides(self) -> None:
        for spec in SETTINGS:
            assert is_known_key(spec.key)
        assert is_known_key(task_type_override_key("skill_auto_draft"))
        assert is_known_key(task_type_override_key("anything"))
        assert not is_known_key("manual_escalation.bogus.key")
        assert not is_known_key("foo.bar")

    def test_coerce_value_accepts_valid_types(self) -> None:
        spec_bool = SETTINGS[0]  # manual_escalation.enabled
        assert coerce_value(spec_bool, True) is True
        with pytest.raises(ValueError):
            coerce_value(spec_bool, "yes")
        spec_float = SETTINGS[-1]  # max_daily_extension_usd
        assert coerce_value(spec_float, 7) == 7.0
        with pytest.raises(ValueError):
            coerce_value(spec_float, True)  # bool subclasses int — must reject

    def test_coerce_task_type_override(self) -> None:
        for v in TASK_TYPE_OVERRIDE_VALUES:
            assert coerce_task_type_override(v) == v
        with pytest.raises(ValueError):
            coerce_task_type_override("nope")

    def test_legacy_aliases_are_documented_for_renamed_keys(self) -> None:
        """Slice 17/21 wrote two keys without the ``manual_escalation.`` prefix.

        The catalog must remember those as aliases so existing rows in
        production environments do not silently lose their override.
        """
        cc = next(
            s
            for s in SETTINGS
            if s.key == "manual_escalation.modes.claude_code.enabled"
        )
        assert "modes.claude_code.enabled" in cc.legacy_aliases
        ext = next(
            s
            for s in SETTINGS
            if s.key == "manual_escalation.budget_extension.enabled"
        )
        assert "budget_extension.enabled" in ext.legacy_aliases


class TestSliderCap:
    def test_days_left_handles_month_end(self) -> None:
        # Last day of January — only "today" remains.
        assert days_left_in_month(date(2026, 1, 31)) == 1

    def test_cap_ratio_matches_spec_formula(
        self, yaml_config: ManualEscalationConfig
    ) -> None:
        today = date(2026, 1, 1)  # 31 days remaining
        cap = max_daily_extension_cap_usd(yaml_config, today)
        # 150 / 31 ≈ 4.83
        assert abs(cap - (150.0 / 31)) < 1e-6


# ---------------------------------------------------------------------------
# Optimistic lock
# ---------------------------------------------------------------------------


class TestOptimisticLock:
    async def test_first_write_with_no_token_inserts(
        self, repo: EscalationRepository
    ) -> None:
        ok, value, updated_at, updated_by = (
            await repo.set_dashboard_setting_with_lock(
                "manual_escalation.enabled",
                True,
                expected_updated_at=None,
                updated_by="nick",
            )
        )
        assert ok is True
        assert value is True
        assert updated_by == "nick"
        # Row should now exist.
        row = await repo.get_dashboard_setting_row("manual_escalation.enabled")
        assert row is not None
        assert row[0] is True
        assert row[1] == updated_at

    async def test_second_write_with_correct_token_succeeds(
        self, repo: EscalationRepository
    ) -> None:
        await repo.upsert_dashboard_setting(
            "manual_escalation.enabled", True, updated_by="boot"
        )
        row = await repo.get_dashboard_setting_row("manual_escalation.enabled")
        assert row is not None
        ok, value, _, updated_by = await repo.set_dashboard_setting_with_lock(
            "manual_escalation.enabled",
            False,
            expected_updated_at=row[1],
            updated_by="nick",
        )
        assert ok is True
        assert value is False
        assert updated_by == "nick"

    async def test_stale_token_returns_conflict_with_current_state(
        self, repo: EscalationRepository
    ) -> None:
        # Browser tab A reads, then tab B writes, then tab A tries to write.
        await repo.upsert_dashboard_setting(
            "manual_escalation.enabled", True, updated_by="boot"
        )
        await repo.upsert_dashboard_setting(
            "manual_escalation.enabled", False, updated_by="b"
        )
        ok, current_value, current_updated_at, current_updated_by = (
            await repo.set_dashboard_setting_with_lock(
                "manual_escalation.enabled",
                True,
                expected_updated_at="1999-01-01T00:00:00+00:00",
                updated_by="a",
            )
        )
        assert ok is False
        assert current_value is False
        assert current_updated_by == "b"
        assert current_updated_at  # non-empty

    async def test_token_supplied_for_missing_row_is_a_conflict(
        self, repo: EscalationRepository
    ) -> None:
        ok, *_ = await repo.set_dashboard_setting_with_lock(
            "manual_escalation.enabled",
            True,
            expected_updated_at="2026-01-01T00:00:00+00:00",
            updated_by="nick",
        )
        assert ok is False  # row does not exist; token assertion fails

    async def test_resolver_falls_back_to_legacy_alias(
        self, repo: EscalationRepository
    ) -> None:
        """A row written under the legacy key must still surface."""
        await repo.upsert_dashboard_setting(
            "modes.claude_code.enabled", False, updated_by="legacy_boot"
        )
        resolver = DashboardSettingResolver(repo)
        value = await resolver.get(
            "manual_escalation.modes.claude_code.enabled", True
        )
        assert value is False
