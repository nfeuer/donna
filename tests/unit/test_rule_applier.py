"""Unit tests for PreferenceApplier."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.orchestrator.input_parser import TaskParseResult
from donna.preferences.rule_applier import _CACHE, PreferenceApplier


def _result(**kwargs) -> TaskParseResult:
    defaults = dict(
        title="Fix car oil change",
        description=None,
        domain="work",
        priority=2,
        deadline=None,
        deadline_type="none",
        estimated_duration=60,
        recurrence=None,
        tags=[],
        prep_work_flag=False,
        agent_eligible=False,
        confidence=0.9,
    )
    defaults.update(kwargs)
    return TaskParseResult(**defaults)


def _rule(field, value, keywords=None, domain=None, confidence=0.9, enabled=True):
    condition: dict = {}
    if keywords:
        condition["keywords"] = keywords
    if domain:
        condition["domain"] = domain
    return {
        "id": "rule-1",
        "rule_type": "domain_override",
        "confidence": confidence,
        "condition": condition,
        "action": {"field": field, "value": value},
    }


@pytest.fixture(autouse=True)
def clear_cache():
    _CACHE.clear()
    yield
    _CACHE.clear()


def _make_applier():
    db = MagicMock()
    return PreferenceApplier(db)


def test_apply_no_rules_returns_unchanged():
    applier = _make_applier()
    result = _result()
    out = applier.apply(result, [])
    assert out == result


def test_apply_keyword_match_overrides_domain():
    applier = _make_applier()
    result = _result(title="Fix car oil change", domain="work")
    rule = _rule("domain", "personal", keywords=["car", "oil"])
    out = applier.apply(result, [rule])
    assert out.domain == "personal"


def test_apply_keyword_no_match_unchanged():
    applier = _make_applier()
    result = _result(title="Team standup meeting", domain="work")
    rule = _rule("domain", "personal", keywords=["car"])
    out = applier.apply(result, [rule])
    assert out.domain == "work"


def test_apply_highest_confidence_rule_wins():
    applier = _make_applier()
    result = _result(title="oil change", domain="work")
    rules = [
        _rule("domain", "personal", keywords=["oil"], confidence=0.9),
        _rule("domain", "family", keywords=["oil"], confidence=0.7),
    ]
    # Rules sorted by confidence descending (already in that order here).
    out = applier.apply(result, rules)
    assert out.domain == "personal"


def test_apply_only_first_matching_rule_per_field():
    """Once a field is set by one rule, subsequent rules for the same field are ignored."""
    applier = _make_applier()
    result = _result(title="oil change", domain="work")
    rules = [
        _rule("domain", "personal", keywords=["oil"], confidence=0.9),
        _rule("domain", "family", keywords=["change"], confidence=0.8),
    ]
    out = applier.apply(result, rules)
    assert out.domain == "personal"  # first rule wins


def test_apply_domain_condition_restricts_match():
    """Rule with domain='work' only fires if task domain is 'work'."""
    applier = _make_applier()
    rule = _rule("priority", 4, domain="work")

    work_result = _result(domain="work")
    personal_result = _result(domain="personal")

    assert applier.apply(work_result, [rule]).priority == 4
    assert applier.apply(personal_result, [rule]).priority == 2  # unchanged


def test_apply_is_non_destructive():
    """The original TaskParseResult is not mutated."""
    applier = _make_applier()
    result = _result(title="oil change", domain="work")
    rule = _rule("domain", "personal", keywords=["oil"])
    out = applier.apply(result, [rule])
    assert result.domain == "work"  # unchanged
    assert out.domain == "personal"  # new instance


def test_apply_unknown_field_ignored():
    """Rules referencing a field not in TaskParseResult are silently skipped."""
    applier = _make_applier()
    result = _result()
    rule = _rule("nonexistent_field", "value")
    out = applier.apply(result, [rule])
    assert out == result


@pytest.mark.asyncio
async def test_apply_for_user_loads_and_applies():
    """apply_for_user() calls load_rules and apply."""
    applier = _make_applier()
    rule = _rule("domain", "personal", keywords=["car"])
    applier.load_rules = AsyncMock(return_value=[rule])

    result = _result(title="car wash", domain="work")
    out = await applier.apply_for_user(result, "nick")
    assert out.domain == "personal"
