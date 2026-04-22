"""AlertEvaluator — evaluates automation.alert_conditions against a run output.

Spec §6.9 alert-condition DSL:
  - Leaf: {field, op, value} where op in (==, !=, <, <=, >, >=, contains, exists).
  - Compound: {all_of: [child, ...]} or {any_of: [child, ...]}.
  - An empty dict means "no alert conditions" and evaluates to False.
"""

from __future__ import annotations

from typing import Any

_LEAF_OPS = {"==", "!=", "<", "<=", ">", ">=", "contains", "exists"}


class InvalidAlertExpressionError(ValueError):
    """Raised when the alert expression has unknown ops or malformed shape."""


class AlertEvaluator:
    def evaluate(self, expression: Any, output: dict[str, Any]) -> bool:
        if not isinstance(expression, dict) or not expression:
            return False
        return self._check(expression, output)

    def _check(self, node: Any, output: dict[str, Any]) -> bool:
        if not isinstance(node, dict):
            raise InvalidAlertExpressionError(
                f"expected dict, got {type(node).__name__}"
            )
        if "all_of" in node:
            children = node["all_of"]
            if not isinstance(children, list):
                raise InvalidAlertExpressionError("all_of must be a list")
            if not children:
                return False
            return all(self._check(c, output) for c in children)
        if "any_of" in node:
            children = node["any_of"]
            if not isinstance(children, list):
                raise InvalidAlertExpressionError("any_of must be a list")
            if not children:
                return False
            return any(self._check(c, output) for c in children)
        if "field" in node and "op" in node:
            return self._check_leaf(node, output)
        raise InvalidAlertExpressionError(f"unknown node shape: keys={list(node)}")

    def _check_leaf(self, leaf: dict[str, Any], output: dict[str, Any]) -> bool:
        op = leaf["op"]
        if op not in _LEAF_OPS:
            raise InvalidAlertExpressionError(f"unknown op {op!r}")
        field_path = leaf["field"]
        present, actual = _walk(output, field_path)
        if op == "exists":
            return present
        if not present:
            return False
        value = leaf.get("value")
        if op == "==":
            return bool(actual == value)
        if op == "!=":
            return bool(actual != value)
        if op == "contains":
            try:
                return bool(value in actual)
            except TypeError:
                return False
        try:
            if op == "<":
                return bool(actual < value)
            if op == "<=":
                return bool(actual <= value)
            if op == ">":
                return bool(actual > value)
            if op == ">=":
                return bool(actual >= value)
        except TypeError:
            return False
        return False


def _walk(output: dict[str, Any], dotted_path: str) -> tuple[bool, Any]:
    """Walk a.b.c into nested dicts. Returns (present, value)."""
    parts = dotted_path.split(".")
    cur: Any = output
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur
