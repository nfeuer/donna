"""Infer a structural JSON Schema from an example value.

Used when a captured ``skill_run.final_output`` needs an
``expected_output_shape`` for a newly created captured-run fixture. The
inferred schema validates names, types, required fields, and nested
structure — it does NOT pin exact values (see spec §5.2 convention).

v1 is intentionally minimal. Arrays are described by the first element's
schema; empty arrays get ``{"type": "array"}`` only. Heterogeneous unions
are not supported — revisit when a real fixture demonstrates the need.
"""

from __future__ import annotations

from typing import Any


def json_to_schema(value: Any) -> dict:
    """Infer a structural JSON Schema from ``value``."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):  # must come before int check
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array"}
        return {"type": "array", "items": json_to_schema(value[0])}
    if isinstance(value, dict):
        props = {k: json_to_schema(v) for k, v in value.items()}
        return {
            "type": "object",
            "properties": props,
            "required": list(value.keys()),
        }
    return {}
