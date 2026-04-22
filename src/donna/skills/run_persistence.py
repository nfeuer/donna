"""SkillRunRepository — writes skill_run and skill_step_result rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()


class SkillRunRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def start_run(
        self,
        skill_id: str,
        skill_version_id: str,
        inputs: dict,
        user_id: str,
        task_id: str | None,
        automation_run_id: str | None,
    ) -> str:
        """Create a skill_run row with status=running; return the new run_id."""
        run_id = str(uuid6.uuid7())
        now = datetime.now(UTC).isoformat()

        await self._conn.execute(
            """
            INSERT INTO skill_run (
                id, skill_id, skill_version_id, task_id, automation_run_id,
                status, total_latency_ms, total_cost_usd,
                state_object, tool_result_cache, final_output,
                escalation_reason, error, user_id, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, 'running', NULL, NULL, ?, NULL, NULL,
                      NULL, NULL, ?, ?, NULL)
            """,
            (
                run_id, skill_id, skill_version_id, task_id, automation_run_id,
                json.dumps({"inputs": inputs}),
                user_id, now,
            ),
        )
        await self._conn.commit()
        return run_id

    async def record_step(
        self,
        skill_run_id: str,
        step_name: str,
        step_index: int,
        step_kind: str,
        output: dict | None,
        latency_ms: int,
        validation_status: str,
        invocation_log_id: str | None = None,
        tool_calls: list | None = None,
        prompt_tokens: int | None = None,
        error: str | None = None,
    ) -> str:
        step_id = str(uuid6.uuid7())
        now = datetime.now(UTC).isoformat()

        await self._conn.execute(
            """
            INSERT INTO skill_step_result (
                id, skill_run_id, step_name, step_index, step_kind,
                invocation_log_id, prompt_tokens, output, tool_calls,
                latency_ms, validation_status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id, skill_run_id, step_name, step_index, step_kind,
                invocation_log_id, prompt_tokens,
                json.dumps(output) if output is not None else None,
                json.dumps(tool_calls) if tool_calls is not None else None,
                latency_ms, validation_status, error, now,
            ),
        )
        await self._conn.commit()
        return step_id

    async def finish_run(
        self,
        skill_run_id: str,
        status: str,
        final_output: Any,
        state_object: dict,
        tool_result_cache: dict,
        total_latency_ms: int,
        total_cost_usd: float,
        escalation_reason: str | None,
        error: str | None,
    ) -> None:
        now = datetime.now(UTC).isoformat()

        await self._conn.execute(
            """
            UPDATE skill_run
               SET status = ?, final_output = ?, state_object = ?,
                   tool_result_cache = ?, total_latency_ms = ?, total_cost_usd = ?,
                   escalation_reason = ?, error = ?, finished_at = ?
             WHERE id = ?
            """,
            (
                status,
                json.dumps(final_output) if final_output is not None else None,
                json.dumps(state_object),
                json.dumps(tool_result_cache) if tool_result_cache else None,
                total_latency_ms, total_cost_usd,
                escalation_reason, error, now,
                skill_run_id,
            ),
        )
        await self._conn.commit()
