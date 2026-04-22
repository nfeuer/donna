"""Lightweight dataclasses and row mappers for the capability registry."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CAPABILITY_COLUMNS = (
    "id",
    "name",
    "description",
    "input_schema",
    "trigger_type",
    "default_output_shape",
    "status",
    "embedding",
    "created_at",
    "created_by",
    "notes",
)

SELECT_CAPABILITY = ", ".join(CAPABILITY_COLUMNS)


@dataclass(slots=True)
class CapabilityRow:
    id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    trigger_type: str
    default_output_shape: dict[str, Any] | None
    status: str
    embedding: bytes | None
    created_at: datetime
    created_by: str
    notes: str | None


def row_to_capability(row: Sequence[Any]) -> CapabilityRow:
    return CapabilityRow(
        id=row[0],
        name=row[1],
        description=row[2],
        input_schema=_parse_json(row[3]),
        trigger_type=row[4],
        default_output_shape=_parse_json(row[5]) if row[5] is not None else None,
        status=row[6],
        embedding=row[7],
        created_at=_parse_dt(row[8]),
        created_by=row[9],
        notes=row[10],
    )


def _parse_json(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
