"""AutomationRepository — sole persistence layer for automation rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import structlog
import uuid6

from donna.automations.models import (
    AUTOMATION_COLUMNS,
    AUTOMATION_RUN_COLUMNS,
    SELECT_AUTOMATION,
    SELECT_AUTOMATION_RUN,
    AutomationRow,
    AutomationRunRow,
    row_to_automation,
    row_to_automation_run,
)

logger = structlog.get_logger()


class AlreadyExistsError(Exception):
    """Raised when creating an automation that collides on (user_id, name)."""


class AutomationRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        description: str | None,
        capability_name: str,
        inputs: dict,
        trigger_type: str,
        schedule: str | None,
        alert_conditions: dict,
        alert_channels: list,
        max_cost_per_run_usd: float | None,
        min_interval_seconds: int,
        created_via: str,
        next_run_at: datetime | None = None,
        target_cadence_cron: str | None = None,
        active_cadence_cron: str | None = None,
    ) -> str:
        auto_id = str(uuid6.uuid7())
        now_iso = datetime.now(timezone.utc).isoformat()
        target_cadence_cron = target_cadence_cron or schedule
        active_cadence_cron = active_cadence_cron or schedule
        try:
            await self._conn.execute(
                f"INSERT INTO automation ({SELECT_AUTOMATION}) "
                f"VALUES ({', '.join('?' for _ in AUTOMATION_COLUMNS)})",
                (
                    auto_id, user_id, name, description, capability_name,
                    json.dumps(inputs), trigger_type, schedule,
                    json.dumps(alert_conditions), json.dumps(alert_channels),
                    max_cost_per_run_usd, min_interval_seconds,
                    "active",
                    None,
                    next_run_at.isoformat() if next_run_at else None,
                    0, 0,
                    now_iso, now_iso, created_via,
                    active_cadence_cron,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise AlreadyExistsError(
                f"automation {user_id}/{name} already exists"
            ) from exc
        await self._conn.commit()
        return auto_id

    async def get(self, automation_id: str) -> AutomationRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation WHERE id = ?",
            (automation_id,),
        )
        row = await cursor.fetchone()
        return row_to_automation(row) if row is not None else None

    async def list_all(
        self,
        *,
        status: str | None = None,
        capability_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AutomationRow]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if capability_name is not None:
            clauses.append("capability_name = ?")
            params.append(capability_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation {where} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = await cursor.fetchall()
        return [row_to_automation(r) for r in rows]

    async def list_by_capability(self, capability_name: str) -> list[AutomationRow]:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation WHERE capability_name = ?",
            (capability_name,),
        )
        rows = await cursor.fetchall()
        return [row_to_automation(r) for r in rows]

    async def update_active_cadence(
        self,
        automation_id: str,
        active_cadence_cron: str | None,
        next_run_at: datetime | None,
    ) -> None:
        """Atomically set active_cadence_cron + next_run_at on an automation row."""
        iso = next_run_at.isoformat() if next_run_at is not None else None
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE automation SET active_cadence_cron = ?, next_run_at = ?, "
            "updated_at = ? WHERE id = ?",
            (active_cadence_cron, iso, now_iso, automation_id),
        )
        await self._conn.commit()

    async def list_due(self, now: datetime) -> list[AutomationRow]:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation "
            f"WHERE status = 'active' AND next_run_at IS NOT NULL "
            f"AND next_run_at <= ? "
            f"ORDER BY next_run_at ASC",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        return [row_to_automation(r) for r in rows]

    async def update_fields(self, automation_id: str, **fields) -> None:
        if not fields:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        json_cols = {"inputs", "alert_conditions", "alert_channels"}
        dt_cols = {"last_run_at", "next_run_at"}
        set_clauses: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key in json_cols and value is not None:
                set_clauses.append(f"{key} = ?")
                params.append(json.dumps(value))
            elif key in dt_cols and isinstance(value, datetime):
                set_clauses.append(f"{key} = ?")
                params.append(value.isoformat())
            else:
                set_clauses.append(f"{key} = ?")
                params.append(value)
        set_clauses.append("updated_at = ?")
        params.append(now_iso)
        params.append(automation_id)
        await self._conn.execute(
            f"UPDATE automation SET {', '.join(set_clauses)} WHERE id = ?",
            tuple(params),
        )
        await self._conn.commit()

    async def set_status(self, automation_id: str, status: str) -> None:
        await self.update_fields(automation_id, status=status)

    async def advance_schedule(
        self,
        automation_id: str,
        *,
        last_run_at: datetime,
        next_run_at: datetime | None,
        increment_run_count: bool,
        increment_failure_count: bool,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE automation SET "
            "last_run_at = ?, next_run_at = ?, "
            "run_count = run_count + ?, "
            "failure_count = failure_count + ?, "
            "updated_at = ? WHERE id = ?",
            (
                last_run_at.isoformat(),
                next_run_at.isoformat() if next_run_at else None,
                1 if increment_run_count else 0,
                1 if increment_failure_count else 0,
                now_iso, automation_id,
            ),
        )
        await self._conn.commit()

    async def reset_failure_count(self, automation_id: str) -> None:
        await self.update_fields(automation_id, failure_count=0)

    async def insert_run(
        self,
        *,
        automation_id: str,
        started_at: datetime,
        execution_path: str,
    ) -> str:
        run_id = str(uuid6.uuid7())
        await self._conn.execute(
            f"INSERT INTO automation_run ({SELECT_AUTOMATION_RUN}) "
            f"VALUES ({', '.join('?' for _ in AUTOMATION_RUN_COLUMNS)})",
            (
                run_id, automation_id, started_at.isoformat(),
                None,
                "running", execution_path,
                None, None, None, 0, None, None, None,
            ),
        )
        await self._conn.commit()
        return run_id

    async def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        output: dict | None,
        skill_run_id: str | None,
        invocation_log_id: str | None,
        alert_sent: bool,
        alert_content: str | None,
        error: str | None,
        cost_usd: float | None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE automation_run SET "
            "finished_at = ?, status = ?, output = ?, "
            "skill_run_id = ?, invocation_log_id = ?, "
            "alert_sent = ?, alert_content = ?, error = ?, cost_usd = ? "
            "WHERE id = ?",
            (
                now_iso, status,
                json.dumps(output) if output is not None else None,
                skill_run_id, invocation_log_id,
                1 if alert_sent else 0, alert_content, error, cost_usd,
                run_id,
            ),
        )
        await self._conn.commit()

    async def list_runs(
        self,
        automation_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AutomationRunRow]:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION_RUN} FROM automation_run "
            f"WHERE automation_id = ? "
            f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (automation_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [row_to_automation_run(r) for r in rows]
