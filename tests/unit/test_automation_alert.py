import pytest

from donna.automations.alert import AlertEvaluator, InvalidAlertExpressionError


def test_leaf_equal_true():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "==", "value": 100}
    assert calc.evaluate(expr, {"price": 100}) is True


def test_leaf_equal_false():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "==", "value": 100}
    assert calc.evaluate(expr, {"price": 99}) is False


def test_leaf_less_than_or_equal():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "<=", "value": 100}
    assert calc.evaluate(expr, {"price": 99}) is True
    assert calc.evaluate(expr, {"price": 100}) is True
    assert calc.evaluate(expr, {"price": 101}) is False


def test_leaf_contains_string():
    calc = AlertEvaluator()
    expr = {"field": "title", "op": "contains", "value": "shirt"}
    assert calc.evaluate(expr, {"title": "red cotton shirt"}) is True
    assert calc.evaluate(expr, {"title": "red cotton pants"}) is False


def test_leaf_contains_list():
    calc = AlertEvaluator()
    expr = {"field": "tags", "op": "contains", "value": "sale"}
    assert calc.evaluate(expr, {"tags": ["sale", "new"]}) is True


def test_leaf_exists():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "exists"}
    assert calc.evaluate(expr, {"price": 100}) is True
    assert calc.evaluate(expr, {"name": "shirt"}) is False


def test_dotted_field_path():
    calc = AlertEvaluator()
    expr = {"field": "product.price", "op": "<", "value": 50}
    assert calc.evaluate(expr, {"product": {"price": 42}}) is True
    assert calc.evaluate(expr, {"product": {"price": 60}}) is False


def test_missing_field_evaluates_false_for_comparisons():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "<=", "value": 100}
    assert calc.evaluate(expr, {"name": "x"}) is False


def test_all_of_true_when_every_child_true():
    calc = AlertEvaluator()
    expr = {"all_of": [
        {"field": "price", "op": "<=", "value": 100},
        {"field": "in_stock", "op": "==", "value": True},
    ]}
    assert calc.evaluate(expr, {"price": 50, "in_stock": True}) is True


def test_all_of_false_when_any_child_false():
    calc = AlertEvaluator()
    expr = {"all_of": [
        {"field": "price", "op": "<=", "value": 100},
        {"field": "in_stock", "op": "==", "value": True},
    ]}
    assert calc.evaluate(expr, {"price": 50, "in_stock": False}) is False


def test_any_of_true_when_at_least_one_child_true():
    calc = AlertEvaluator()
    expr = {"any_of": [
        {"field": "price", "op": "<=", "value": 100},
        {"field": "tag", "op": "==", "value": "sale"},
    ]}
    assert calc.evaluate(expr, {"price": 200, "tag": "sale"}) is True
    assert calc.evaluate(expr, {"price": 200, "tag": "regular"}) is False


def test_empty_conditions_return_false():
    calc = AlertEvaluator()
    assert calc.evaluate({}, {"price": 100}) is False


def test_unknown_op_raises():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "lol", "value": 1}
    with pytest.raises(InvalidAlertExpressionError):
        calc.evaluate(expr, {"price": 1})


def test_unknown_compound_raises():
    calc = AlertEvaluator()
    expr = {"only_of": [{"field": "x", "op": "==", "value": 1}]}
    with pytest.raises(InvalidAlertExpressionError):
        calc.evaluate(expr, {"x": 1})


def test_nested_compound():
    calc = AlertEvaluator()
    expr = {"all_of": [
        {"any_of": [
            {"field": "price", "op": "<=", "value": 100},
            {"field": "is_sale", "op": "==", "value": True},
        ]},
        {"field": "in_stock", "op": "==", "value": True},
    ]}
    assert calc.evaluate(expr, {"price": 80, "is_sale": False, "in_stock": True}) is True
    assert calc.evaluate(expr, {"price": 200, "is_sale": True, "in_stock": True}) is True
    assert calc.evaluate(expr, {"price": 80, "is_sale": False, "in_stock": False}) is False


def test_empty_all_of_returns_false():
    calc = AlertEvaluator()
    assert calc.evaluate({"all_of": []}, {"price": 100}) is False


def test_empty_any_of_returns_false():
    calc = AlertEvaluator()
    assert calc.evaluate({"any_of": []}, {"price": 100}) is False
