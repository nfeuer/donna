"""Tests for donna.skills.schema_inference.json_to_schema."""

from __future__ import annotations

from donna.skills.schema_inference import json_to_schema


def test_primitive_types() -> None:
    assert json_to_schema(42) == {"type": "integer"}
    assert json_to_schema(3.14) == {"type": "number"}
    assert json_to_schema("hi") == {"type": "string"}
    assert json_to_schema(True) == {"type": "boolean"}
    assert json_to_schema(None) == {"type": "null"}


def test_flat_object() -> None:
    schema = json_to_schema({"title": "Q2 review", "days": 3})
    assert schema == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "days": {"type": "integer"},
        },
        "required": ["title", "days"],
    }


def test_array_of_objects() -> None:
    schema = json_to_schema([{"price": 100.0, "in_stock": True}])
    assert schema == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "price": {"type": "number"},
                "in_stock": {"type": "boolean"},
            },
            "required": ["price", "in_stock"],
        },
    }


def test_empty_array() -> None:
    assert json_to_schema([]) == {"type": "array"}


def test_empty_object() -> None:
    assert json_to_schema({}) == {"type": "object", "properties": {}, "required": []}


def test_heterogeneous_array_uses_first_element_schema() -> None:
    schema = json_to_schema([1, 2.5])
    assert schema == {"type": "array", "items": {"type": "integer"}}


def test_nested_object() -> None:
    value = {"item": {"name": "shirt", "price": 79.0}}
    schema = json_to_schema(value)
    assert schema["type"] == "object"
    assert schema["properties"]["item"]["type"] == "object"
    assert schema["properties"]["item"]["required"] == ["name", "price"]
