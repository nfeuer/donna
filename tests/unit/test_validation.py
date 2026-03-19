"""Tests for LLM output schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from donna.models.validation import ValidationError, validate_output

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


@pytest.fixture
def task_parse_schema() -> dict:
    with open(SCHEMA_DIR / "task_parse_output.json") as f:
        return json.load(f)


def _valid_output(**overrides: object) -> dict:
    """Return a minimal valid task parse output."""
    base = {
        "title": "Buy milk",
        "domain": "personal",
        "priority": 1,
        "deadline": None,
        "deadline_type": "none",
        "estimated_duration": 15,
        "recurrence": None,
        "tags": [],
        "prep_work_flag": False,
        "agent_eligible": False,
        "confidence": 0.95,
    }
    base.update(overrides)
    return base


class TestValidateOutput:
    def test_valid_output_passes(self, task_parse_schema: dict) -> None:
        result = validate_output(_valid_output(), task_parse_schema)
        assert result["title"] == "Buy milk"

    def test_missing_required_field_raises(self, task_parse_schema: dict) -> None:
        data = _valid_output()
        del data["title"]
        with pytest.raises(ValidationError) as exc_info:
            validate_output(data, task_parse_schema)
        assert len(exc_info.value.errors) > 0
        assert any("title" in e for e in exc_info.value.errors)

    def test_invalid_domain_enum_raises(self, task_parse_schema: dict) -> None:
        data = _valid_output(domain="invalid_domain")
        with pytest.raises(ValidationError):
            validate_output(data, task_parse_schema)

    def test_priority_out_of_range_raises(self, task_parse_schema: dict) -> None:
        data = _valid_output(priority=10)
        with pytest.raises(ValidationError):
            validate_output(data, task_parse_schema)

    def test_priority_below_minimum_raises(self, task_parse_schema: dict) -> None:
        data = _valid_output(priority=0)
        with pytest.raises(ValidationError):
            validate_output(data, task_parse_schema)

    def test_invalid_deadline_type_raises(self, task_parse_schema: dict) -> None:
        data = _valid_output(deadline_type="urgent")
        with pytest.raises(ValidationError):
            validate_output(data, task_parse_schema)

    def test_confidence_out_of_range_raises(self, task_parse_schema: dict) -> None:
        data = _valid_output(confidence=1.5)
        with pytest.raises(ValidationError):
            validate_output(data, task_parse_schema)

    def test_description_nullable(self, task_parse_schema: dict) -> None:
        result = validate_output(_valid_output(description=None), task_parse_schema)
        assert result["description"] is None

    def test_description_string(self, task_parse_schema: dict) -> None:
        result = validate_output(
            _valid_output(description="Get whole milk from store"),
            task_parse_schema,
        )
        assert result["description"] == "Get whole milk from store"

    def test_with_deadline(self, task_parse_schema: dict) -> None:
        result = validate_output(
            _valid_output(
                deadline="2026-03-21T17:00:00",
                deadline_type="hard",
                priority=3,
            ),
            task_parse_schema,
        )
        assert result["deadline_type"] == "hard"
