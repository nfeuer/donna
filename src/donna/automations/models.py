"""Automation + AutomationRun dataclass row mappers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

AUTOMATION_COLUMNS = (
    "id", "user_id", "name", "description", "capability_name",
    "inputs", "trigger_type", "schedule", "alert_conditions",
    "alert_channels", "max_cost_per_run_usd", "min_interval_seconds",
    "status", "last_run_at", "next_run_at", "run_count",
    "failure_count", "created_at", "updated_at", "created_via",
    "active_cadence_cron",
)
SELECT_AUTOMATION = ", ".join(AUTOMATION_COLUMNS)

AUTOMATION_RUN_COLUMNS = (
    "id", "automation_id", "started_at", "finished_at", "status",
    "execution_path", "skill_run_id", "invocation_log_id",
    "output", "alert_sent", "alert_content", "error", "cost_usd",
)
SELECT_AUTOMATION_RUN = ", ".join(AUTOMATION_RUN_COLUMNS)


@dataclass(slots=True)
class AutomationRow:
    id: str
    user_id: str
    name: str
    description: str | None
    capability_name: str
    inputs: dict
    trigger_type: str
    schedule: str | None
    alert_conditions: dict
    alert_channels: list
    max_cost_per_run_usd: float | None
    min_interval_seconds: int
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    run_count: int
    failure_count: int
    created_at: datetime
    updated_at: datetime
    created_via: str
    active_cadence_cron: str | None = None

    @property
    def target_cadence_cron(self) -> str | None:
        """Wave 3 — user's intended cadence. Reads from schedule for backward compat."""
        return self.schedule


@dataclass(slots=True)
class AutomationRunRow:
    id: str
    automation_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    execution_path: str
    skill_run_id: str | None
    invocation_log_id: str | None
    output: dict | None
    alert_sent: bool
    alert_content: str | None
    error: str | None
    cost_usd: float | None


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def row_to_automation(row: tuple) -> AutomationRow:
    return AutomationRow(
        id=row[0], user_id=row[1], name=row[2], description=row[3],
        capability_name=row[4],
        inputs=_parse_json(row[5]) or {},
        trigger_type=row[6], schedule=row[7],
        alert_conditions=_parse_json(row[8]) or {},
        alert_channels=_parse_json(row[9]) or [],
        max_cost_per_run_usd=row[10],
        min_interval_seconds=row[11],
        status=row[12],
        last_run_at=_parse_dt(row[13]),
        next_run_at=_parse_dt(row[14]),
        run_count=row[15], failure_count=row[16],
        created_at=_parse_dt(row[17]),
        updated_at=_parse_dt(row[18]),
        created_via=row[19],
        active_cadence_cron=row[20] if len(row) > 20 else None,
    )


def row_to_automation_run(row: tuple) -> AutomationRunRow:
    return AutomationRunRow(
        id=row[0], automation_id=row[1],
        started_at=_parse_dt(row[2]),
        finished_at=_parse_dt(row[3]),
        status=row[4], execution_path=row[5],
        skill_run_id=row[6], invocation_log_id=row[7],
        output=_parse_json(row[8]),
        alert_sent=bool(row[9]),
        alert_content=row[10], error=row[11], cost_usd=row[12],
    )
