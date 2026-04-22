from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SKILL_COLUMNS = (
    "id", "capability_name", "current_version_id", "state",
    "requires_human_gate", "baseline_agreement", "created_at", "updated_at",
)
SELECT_SKILL = ", ".join(SKILL_COLUMNS)

SKILL_VERSION_COLUMNS = (
    "id", "skill_id", "version_number", "yaml_backbone", "step_content",
    "output_schemas", "created_by", "changelog", "created_at",
)
SELECT_SKILL_VERSION = ", ".join(SKILL_VERSION_COLUMNS)


@dataclass(slots=True)
class SkillRow:
    id: str
    capability_name: str
    current_version_id: str | None
    state: str
    requires_human_gate: bool
    baseline_agreement: float | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SkillVersionRow:
    id: str
    skill_id: str
    version_number: int
    yaml_backbone: str
    step_content: dict[str, Any]
    output_schemas: dict[str, Any]
    created_by: str
    changelog: str | None
    created_at: datetime


def row_to_skill(row: Sequence[Any]) -> SkillRow:
    return SkillRow(
        id=row[0], capability_name=row[1], current_version_id=row[2],
        state=row[3], requires_human_gate=bool(row[4]),
        baseline_agreement=row[5], created_at=_parse_dt(row[6]), updated_at=_parse_dt(row[7]),
    )


def row_to_skill_version(row: Sequence[Any]) -> SkillVersionRow:
    return SkillVersionRow(
        id=row[0], skill_id=row[1], version_number=row[2],
        yaml_backbone=row[3], step_content=_parse_json(row[4]),
        output_schemas=_parse_json(row[5]), created_by=row[6],
        changelog=row[7], created_at=_parse_dt(row[8]),
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
