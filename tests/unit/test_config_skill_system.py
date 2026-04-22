"""Unit tests for SkillSystemConfig loading and capability threshold wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import yaml

from donna.capabilities.matcher import (
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    CapabilityMatcher,
    MatchConfidence,
)
from donna.capabilities.models import CapabilityRow
from donna.capabilities.registry import CapabilityRegistry
from donna.config import SkillSystemConfig, load_skill_system_config

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_skills_yaml(tmp_path: Path, data: dict) -> None:
    with open(tmp_path / "skills.yaml", "w") as f:
        yaml.safe_dump(data, f)


def _cap(name: str) -> CapabilityRow:
    return CapabilityRow(
        id="id-" + name,
        name=name,
        description="desc " + name,
        input_schema={},
        trigger_type="on_message",
        default_output_shape=None,
        status="active",
        embedding=None,
        created_at=datetime.now(UTC),
        created_by="seed",
        notes=None,
    )


# ── load_skill_system_config tests ───────────────────────────────────────────


def test_load_skill_system_config_reads_yaml(tmp_path: Path) -> None:
    _write_skills_yaml(tmp_path, {
        "enabled": True,
        "match_confidence_high": 0.88,
        "match_confidence_medium": 0.55,
        "similarity_audit_threshold": 0.70,
        "seed_skills_initial_state": "active",
        "sandbox_promotion_min_runs": 30,
        "nightly_run_hour_utc": 4,
    })

    cfg = load_skill_system_config(tmp_path)

    assert cfg.enabled is True
    assert cfg.match_confidence_high == 0.88
    assert cfg.match_confidence_medium == 0.55
    assert cfg.similarity_audit_threshold == 0.70
    assert cfg.seed_skills_initial_state == "active"
    assert cfg.sandbox_promotion_min_runs == 30
    assert cfg.nightly_run_hour_utc == 4


def test_load_skill_system_config_missing_file_uses_defaults(tmp_path: Path) -> None:
    cfg = load_skill_system_config(tmp_path)

    assert cfg.enabled is False
    assert cfg.match_confidence_high == 0.75
    assert cfg.match_confidence_medium == 0.40
    assert cfg.similarity_audit_threshold == 0.80
    assert cfg.seed_skills_initial_state == "sandbox"
    assert cfg.shadow_sample_rate_trusted == 0.05
    assert cfg.sandbox_promotion_min_runs == 20
    assert cfg.sandbox_promotion_validity_rate == 0.90
    assert cfg.shadow_primary_promotion_min_runs == 100
    assert cfg.shadow_primary_promotion_agreement_rate == 0.85
    assert cfg.degradation_rolling_window == 30
    assert cfg.degradation_ci_confidence == 0.95
    assert cfg.auto_draft_daily_cap == 50
    assert cfg.auto_draft_min_expected_savings_usd == 5.0
    assert cfg.auto_draft_fixture_pass_rate == 0.80
    assert cfg.nightly_run_hour_utc == 3


def test_load_skill_system_config_partial_yaml(tmp_path: Path) -> None:
    _write_skills_yaml(tmp_path, {
        "match_confidence_high": 0.92,
        "auto_draft_daily_cap": 100,
    })

    cfg = load_skill_system_config(tmp_path)

    # Overridden fields from yaml
    assert cfg.match_confidence_high == 0.92
    assert cfg.auto_draft_daily_cap == 100

    # All other fields should be defaults
    assert cfg.enabled is False
    assert cfg.match_confidence_medium == 0.40
    assert cfg.similarity_audit_threshold == 0.80
    assert cfg.nightly_run_hour_utc == 3


# ── CapabilityMatcher config wiring tests ────────────────────────────────────


async def test_capability_matcher_uses_config_thresholds() -> None:
    """Custom thresholds from config shift the confidence bands."""
    config = SkillSystemConfig(match_confidence_high=0.9, match_confidence_medium=0.5)
    registry = AsyncMock()
    matcher = CapabilityMatcher(registry, config=config)

    # 0.85 is below the new HIGH threshold of 0.9 → MEDIUM
    assert matcher._classify_confidence(0.85) == MatchConfidence.MEDIUM
    # 0.45 is below the new MEDIUM threshold of 0.5 → LOW
    assert matcher._classify_confidence(0.45) == MatchConfidence.LOW
    # 0.95 is above HIGH threshold of 0.9 → HIGH
    assert matcher._classify_confidence(0.95) == MatchConfidence.HIGH


async def test_capability_matcher_defaults_without_config() -> None:
    """Without config, module-level constants are used as thresholds."""
    registry = AsyncMock()
    matcher = CapabilityMatcher(registry)

    assert matcher._high == HIGH_CONFIDENCE_THRESHOLD
    assert matcher._medium == MEDIUM_CONFIDENCE_THRESHOLD

    # Default HIGH = 0.75
    assert matcher._classify_confidence(0.75) == MatchConfidence.HIGH
    assert matcher._classify_confidence(0.74) == MatchConfidence.MEDIUM
    # Default MEDIUM = 0.40
    assert matcher._classify_confidence(0.40) == MatchConfidence.MEDIUM
    assert matcher._classify_confidence(0.39) == MatchConfidence.LOW


async def test_capability_matcher_config_thresholds_via_match() -> None:
    """End-to-end: config thresholds affect match() result classification."""
    config = SkillSystemConfig(match_confidence_high=0.9, match_confidence_medium=0.5)
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("some_skill"), 0.85)]

    matcher = CapabilityMatcher(registry, config=config)
    result = await matcher.match("do the thing")

    # 0.85 < 0.9 (high) but >= 0.5 (medium) → MEDIUM, best_match is returned
    assert result.confidence == MatchConfidence.MEDIUM
    assert result.best_match is not None
    assert result.best_match.name == "some_skill"


# ── CapabilityRegistry similarity threshold tests ────────────────────────────


def test_capability_registry_uses_config_similarity_threshold() -> None:
    """Config similarity_audit_threshold overrides the class-level constant."""
    config = SkillSystemConfig(similarity_audit_threshold=0.65)
    mock_conn = MagicMock()

    reg = CapabilityRegistry(mock_conn, config=config)

    assert reg._similarity_threshold == 0.65


def test_capability_registry_defaults_without_config() -> None:
    """Without config, the class attribute SIMILARITY_THRESHOLD is used."""
    mock_conn = MagicMock()
    reg = CapabilityRegistry(mock_conn)

    assert reg._similarity_threshold == CapabilityRegistry.SIMILARITY_THRESHOLD
    assert reg._similarity_threshold == 0.80
