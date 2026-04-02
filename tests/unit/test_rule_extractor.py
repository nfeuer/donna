"""Unit tests for PreferenceRuleExtractor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.preferences.rule_extractor import PreferenceRuleExtractor


def _make_extractor(db=None, router=None, project_root=None):
    db = db or MagicMock()
    router = router or MagicMock()
    project_root = project_root or Path("/tmp")
    return PreferenceRuleExtractor(db, router, "nick", project_root)


@pytest.mark.asyncio
async def test_extract_no_corrections_returns_empty():
    """When correction_log is empty, extract() returns [] without calling LLM."""
    extractor = _make_extractor()
    extractor._load_unprocessed_corrections = AsyncMock(return_value=[])
    result = await extractor.extract()
    assert result == []


@pytest.mark.asyncio
async def test_extract_skips_fields_below_min_corrections():
    """Only 2 corrections for a field — below min_corrections=3 — no LLM call."""
    extractor = _make_extractor()
    corrections = [
        {"id": "c1", "field_corrected": "priority", "original_value": "2",
         "corrected_value": "4", "task_type": "parse_task", "input_text": "a", "timestamp": "t"},
        {"id": "c2", "field_corrected": "priority", "original_value": "2",
         "corrected_value": "4", "task_type": "parse_task", "input_text": "b", "timestamp": "t"},
    ]
    extractor._load_unprocessed_corrections = AsyncMock(return_value=corrections)
    extractor._call_llm = AsyncMock()

    result = await extractor.extract()

    assert result == []
    extractor._call_llm.assert_not_called()


@pytest.mark.asyncio
async def test_extract_calls_llm_with_sufficient_corrections():
    """With ≥ 3 corrections for one field, the LLM is called."""
    extractor = _make_extractor()
    corrections = [
        {"id": f"c{i}", "field_corrected": "domain", "original_value": "work",
         "corrected_value": "personal", "task_type": "parse_task",
         "input_text": f"input {i}", "timestamp": "t"}
        for i in range(3)
    ]
    extractor._load_unprocessed_corrections = AsyncMock(return_value=corrections)
    extractor._load_existing_rules = AsyncMock(return_value=[])
    extractor._save_rules = AsyncMock(return_value=["rule-uuid"])
    extractor._call_llm = AsyncMock(return_value=[
        {
            "rule_type": "domain_override",
            "rule_text": "Personal tasks",
            "confidence": 0.9,
            "condition": {"keywords": ["oil"]},
            "action": {"field": "domain", "value": "personal"},
            "supporting_correction_ids": ["c0", "c1", "c2"],
        }
    ])

    result = await extractor.extract()

    extractor._call_llm.assert_called_once()
    assert result == ["rule-uuid"]


@pytest.mark.asyncio
async def test_extract_filters_low_confidence_rules():
    """Rules below min_confidence=0.7 are discarded before saving."""
    extractor = _make_extractor()
    corrections = [
        {"id": f"c{i}", "field_corrected": "domain", "original_value": "work",
         "corrected_value": "personal", "task_type": "parse_task",
         "input_text": f"x{i}", "timestamp": "t"}
        for i in range(3)
    ]
    extractor._load_unprocessed_corrections = AsyncMock(return_value=corrections)
    extractor._load_existing_rules = AsyncMock(return_value=[])
    extractor._save_rules = AsyncMock(return_value=[])
    extractor._call_llm = AsyncMock(return_value=[
        {
            "rule_type": "domain_override",
            "rule_text": "Low confidence rule",
            "confidence": 0.5,
            "condition": {},
            "action": {"field": "domain", "value": "personal"},
            "supporting_correction_ids": ["c0", "c1", "c2"],
        }
    ])

    result = await extractor.extract()

    extractor._save_rules.assert_not_called()
    assert result == []


@pytest.mark.asyncio
async def test_save_rules_marks_corrections_as_processed():
    """_save_rules updates correction_log.rule_extracted for each correction."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()

    db = MagicMock()
    db.connection = conn

    extractor = _make_extractor(db=db)

    rule = {
        "rule_type": "domain_override",
        "rule_text": "Test rule",
        "confidence": 0.9,
        "condition": {},
        "action": {"field": "domain", "value": "personal"},
        "supporting_correction_ids": ["c1", "c2"],
    }

    rule_ids = await extractor._save_rules([rule])

    assert len(rule_ids) == 1
    # Verify UPDATE correction_log was called twice (once per correction id)
    calls = [str(c) for c in conn.execute.call_args_list]
    update_calls = [c for c in calls if "UPDATE correction_log" in c]
    assert len(update_calls) == 2
