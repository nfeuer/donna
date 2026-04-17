"""Tests for the shared Jinja rendering helper in donna.skills._render."""

from __future__ import annotations

import pytest
import jinja2

from donna.skills._render import render_value


# ---------------------------------------------------------------------------
# 1. Plain string interpolation (preserve_types=False path)
# ---------------------------------------------------------------------------

def test_plain_string_interpolation():
    result = render_value("hello {{ name }}", {"name": "world"}, preserve_types=False)
    assert result == "hello world"


# ---------------------------------------------------------------------------
# 2. preserve_types=True: whole-expression list is returned as a list
# ---------------------------------------------------------------------------

def test_preserve_types_list():
    result = render_value(
        "{{ state.items }}",
        {"state": {"items": [1, 2, 3]}},
        preserve_types=True,
    )
    assert result == [1, 2, 3]
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 3. preserve_types=True: whole-expression dict is returned as a dict
# ---------------------------------------------------------------------------

def test_preserve_types_dict():
    result = render_value(
        "{{ state.obj }}",
        {"state": {"obj": {"a": 1}}},
        preserve_types=True,
    )
    assert result == {"a": 1}
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 4. preserve_types=False: whole-expression returns string regardless of type
# ---------------------------------------------------------------------------

def test_preserve_types_false_returns_string():
    result = render_value(
        "{{ state.obj }}",
        {"state": {"obj": {"a": 1}}},
        preserve_types=False,
    )
    assert isinstance(result, str)
    # The exact string representation of a dict — just confirm it's a string
    assert "a" in result


# ---------------------------------------------------------------------------
# 5. _AttrDict: dict key named 'items' must shadow the dict.items builtin
# ---------------------------------------------------------------------------

def test_attrdict_prefers_key_over_builtin():
    result = render_value(
        "{{ inputs.items }}",
        {"inputs": {"items": ["a"]}},
        preserve_types=True,
    )
    assert result == ["a"]
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 6. Nested dict: each leaf value is rendered
# ---------------------------------------------------------------------------

def test_nested_dict_traverses():
    result = render_value(
        {"url": "{{ x }}", "nested": {"v": "{{ y }}"}},
        {"x": "https://example.com", "y": "42"},
        preserve_types=False,
    )
    assert result == {"url": "https://example.com", "nested": {"v": "42"}}


# ---------------------------------------------------------------------------
# 7. List of string templates: all elements are rendered
# ---------------------------------------------------------------------------

def test_nested_list_traverses():
    result = render_value(
        ["{{ a }}", "static", "{{ b }}"],
        {"a": "first", "b": "last"},
        preserve_types=False,
    )
    assert result == ["first", "static", "last"]


# ---------------------------------------------------------------------------
# 8. Scalar pass-through: int / bool / None come back unchanged
# ---------------------------------------------------------------------------

def test_scalar_passthrough():
    assert render_value(42, {}) == 42
    assert render_value(True, {}) is True
    assert render_value(None, {}) is None


# ---------------------------------------------------------------------------
# 9. StrictUndefined: referencing a missing variable raises UndefinedError
# ---------------------------------------------------------------------------

def test_strict_undefined_raises():
    with pytest.raises(jinja2.UndefinedError):
        render_value("{{ missing }}", {})


# ---------------------------------------------------------------------------
# 10. Scalar whole-expression stringifies (e.g. {{ 42 }} → "42")
# ---------------------------------------------------------------------------

def test_scalar_whole_expression_stringifies():
    result = render_value("{{ 42 }}", {}, preserve_types=True)
    assert result == "42"
    assert isinstance(result, str)
