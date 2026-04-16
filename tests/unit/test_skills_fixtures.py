import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.fixtures import (
    FixtureLoader, FixtureValidationReport, validate_against_fixtures,
)


async def test_load_fixtures_from_directory(tmp_path: Path):
    fix_dir = tmp_path / "fixtures"
    fix_dir.mkdir()
    (fix_dir / "case_a.json").write_text(json.dumps({
        "input": {"raw_text": "hello"},
        "expected_output_shape": {"title": "string"},
    }))
    (fix_dir / "case_b.json").write_text(json.dumps({
        "input": {"raw_text": "goodbye"},
        "expected_output_shape": {"title": "string"},
    }))

    loader = FixtureLoader()
    fixtures = loader.load_from_directory(fix_dir)
    assert len(fixtures) == 2
    assert {f.case_name for f in fixtures} == {"case_a", "case_b"}


async def test_load_fixtures_skips_malformed(tmp_path: Path):
    fix_dir = tmp_path / "fixtures"
    fix_dir.mkdir()
    (fix_dir / "good.json").write_text(json.dumps({"input": {"x": 1}}))
    (fix_dir / "broken.json").write_text("not valid json{{")

    loader = FixtureLoader()
    fixtures = loader.load_from_directory(fix_dir)
    assert len(fixtures) == 1
    assert fixtures[0].case_name == "good"


async def test_load_fixtures_returns_empty_for_missing_dir(tmp_path: Path):
    loader = FixtureLoader()
    fixtures = loader.load_from_directory(tmp_path / "nonexistent")
    assert fixtures == []


async def test_validate_against_fixtures_passes():
    skill = MagicMock(id="s1", capability_name="parse_task", current_version_id="v1")
    version = MagicMock(id="v1")
    executor = AsyncMock()
    executor.execute.return_value = MagicMock(
        status="succeeded",
        final_output={"title": "Q2 review"},
    )

    loader = FixtureLoader()
    fixtures = [loader._make_fixture(
        "case_a", {"raw_text": "Q2"},
        {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}},
    )]

    report = await validate_against_fixtures(skill, executor, fixtures, version=version)

    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0


async def test_validate_against_fixtures_detects_failure():
    skill = MagicMock(id="s1", capability_name="parse_task", current_version_id="v1")
    version = MagicMock(id="v1")
    executor = AsyncMock()
    executor.execute.return_value = MagicMock(
        status="succeeded",
        final_output={"wrong_field": "x"},
    )

    loader = FixtureLoader()
    fixtures = [loader._make_fixture(
        "case_a", {"raw_text": "Q2"},
        {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}},
    )]

    report = await validate_against_fixtures(skill, executor, fixtures, version=version)

    assert report.failed == 1
    assert report.failure_details[0].case_name == "case_a"


async def test_validate_skill_run_failure_counts_as_failure():
    skill = MagicMock()
    version = MagicMock()
    executor = AsyncMock()
    executor.execute.return_value = MagicMock(
        status="failed", error="something broke",
        final_output=None, escalation_reason=None,
    )

    loader = FixtureLoader()
    fixtures = [loader._make_fixture("x", {"raw_text": "Q2"}, None)]

    report = await validate_against_fixtures(skill, executor, fixtures, version=version)

    assert report.failed == 1
