"""Structured invocation logger for every LLM call.

Writes to the invocation_log table. Every API call is tracked per CLAUDE.md.
See docs/model-layer.md Section 4.3.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class InvocationMetadata:
    """Data captured from every LLM invocation."""

    task_type: str
    model_alias: str
    model_actual: str
    input_hash: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    user_id: str
    task_id: str | None = None
    output: dict[str, Any] | None = None
    quality_score: float | None = None
    is_shadow: bool = False
    eval_session_id: str | None = None
    spot_check_queued: bool = False
    queue_wait_ms: int | None = None
    interrupted: bool = False
    chain_id: str | None = None
    caller: str | None = None


class InvocationLogger:
    """Writes LLM invocation records to the invocation_log table."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def log(self, metadata: InvocationMetadata) -> str:
        """Write an invocation record. Returns the generated ID."""
        invocation_id = str(uuid6.uuid7())
        now = datetime.utcnow().isoformat()

        await self._conn.execute(
            """INSERT INTO invocation_log
            (id, timestamp, task_type, task_id, model_alias, model_actual,
             input_hash, latency_ms, tokens_in, tokens_out, cost_usd,
             output, quality_score, is_shadow, eval_session_id,
             spot_check_queued, user_id,
             queue_wait_ms, interrupted, chain_id, caller)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invocation_id,
                now,
                metadata.task_type,
                metadata.task_id,
                metadata.model_alias,
                metadata.model_actual,
                metadata.input_hash,
                metadata.latency_ms,
                metadata.tokens_in,
                metadata.tokens_out,
                metadata.cost_usd,
                json.dumps(metadata.output) if metadata.output is not None else None,
                metadata.quality_score,
                metadata.is_shadow,
                metadata.eval_session_id,
                metadata.spot_check_queued,
                metadata.user_id,
                metadata.queue_wait_ms,
                metadata.interrupted,
                metadata.chain_id,
                metadata.caller,
            ),
        )
        await self._conn.commit()

        logger.info(
            "llm_invocation_logged",
            invocation_id=invocation_id,
            task_type=metadata.task_type,
            model_alias=metadata.model_alias,
            latency_ms=metadata.latency_ms,
            cost_usd=metadata.cost_usd,
        )

        return invocation_id
