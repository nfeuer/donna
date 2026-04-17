"""Verify AlertEvaluator accepts the DSL shape the challenger + novelty judge emit.

Wave 3 bug-fix: the earlier challenger_parse schema described a free-form
``{expression, channels}`` shape that AlertEvaluator does not understand.
Schemas + prompts now require the DSL shape below; this test locks in that
AlertEvaluator actually evaluates it.
"""
from __future__ import annotations

from donna.automations.alert import AlertEvaluator


def test_terminal_alert_dsl_fires_on_match() -> None:
    evaluator = AlertEvaluator()
    cond = {"field": "triggers_alert", "op": "==", "value": True}
    assert evaluator.evaluate(cond, {"triggers_alert": True}) is True
    assert evaluator.evaluate(cond, {"triggers_alert": False}) is False


def test_all_of_composite_alert_dsl() -> None:
    evaluator = AlertEvaluator()
    cond = {
        "all_of": [
            {"field": "has_match", "op": "==", "value": True},
            {"field": "count", "op": ">=", "value": 1},
        ]
    }
    assert evaluator.evaluate(cond, {"has_match": True, "count": 3}) is True
    assert evaluator.evaluate(cond, {"has_match": True, "count": 0}) is False
    assert evaluator.evaluate(cond, {"has_match": False, "count": 5}) is False


def test_any_of_composite_alert_dsl() -> None:
    evaluator = AlertEvaluator()
    cond = {
        "any_of": [
            {"field": "urgent", "op": "==", "value": True},
            {"field": "severity", "op": ">=", "value": 3},
        ]
    }
    assert evaluator.evaluate(cond, {"urgent": False, "severity": 4}) is True
    assert evaluator.evaluate(cond, {"urgent": True, "severity": 1}) is True
    assert evaluator.evaluate(cond, {"urgent": False, "severity": 1}) is False


def test_dotted_field_path_walks_nested_dicts() -> None:
    evaluator = AlertEvaluator()
    cond = {"field": "result.new_matches", "op": ">", "value": 0}
    assert evaluator.evaluate(cond, {"result": {"new_matches": 2}}) is True
    assert evaluator.evaluate(cond, {"result": {"new_matches": 0}}) is False
    # Missing path -> not present -> False (not exists-op, so no fire).
    assert evaluator.evaluate(cond, {"result": {}}) is False
