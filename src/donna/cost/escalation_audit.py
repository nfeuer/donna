"""Audit-log helper for the over-budget escalation lifecycle.

Every state transition in slice 17 (offered, resolved, timed-out)
writes an :class:`~donna.tasks.db_models.InvocationLog` row with
``task_type='escalation_lifecycle'`` and a JSON payload describing the
event. These rows are excluded from cost aggregations because they
carry no LLM spend.

Realizes docs/superpowers/specs/manual-escalation.md §10.10.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()

ESCALATION_TASK_TYPE = "escalation_lifecycle"
"""Sentinel ``task_type`` used for escalation audit rows.

Excluded from :meth:`donna.cost.tracker.CostTracker.get_daily_cost`
sums by callers that pass ``exclude_task_types``.
"""

# Event names used as keys in invocation_log.output payloads.
EVENT_OFFERED = "escalation_offered"
EVENT_RESOLVED = "escalation_resolved"
EVENT_TIMED_OUT = "escalation_timed_out"
EVENT_OWNER_MISMATCH = "escalation_owner_mismatch"
EVENT_EXTENSION_GRANTED = "extension_granted"
EVENT_EXTENSION_VOIDED = "extension_voided"


async def write_escalation_event(
    conn: aiosqlite.Connection,
    *,
    event: str,
    escalation_request_id: int,
    correlation_id: str,
    user_id: str,
    task_id: str | None,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> str:
    """Insert one ``escalation_lifecycle`` audit row.

    Args:
        conn: Shared aiosqlite connection.
        event: One of the ``EVENT_*`` constants.
        escalation_request_id: FK into ``escalation_request``.
        correlation_id: ULID/UUIDv7 stored in ``input_hash`` (truncated).
        user_id: Owner of the escalation.
        task_id: Optional task ID; mirrored into ``invocation_log.task_id``.
        payload: Free-form event data merged with ``{"event": event}``.
        now: Override timestamp (tests).

    Returns:
        The new ``invocation_log.id`` (UUIDv7 string).
    """
    body = {"event": event, **payload}
    invocation_id = str(uuid6.uuid7())
    ts = (now or datetime.now(tz=UTC)).isoformat()
    await conn.execute(
        """
        INSERT INTO invocation_log (
            id, timestamp, task_type, task_id, model_alias, model_actual,
            input_hash, latency_ms, tokens_in, tokens_out, cost_usd,
            output, is_shadow, spot_check_queued, user_id,
            escalation_request_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0.0, ?, 0, 0, ?, ?)
        """,
        (
            invocation_id,
            ts,
            ESCALATION_TASK_TYPE,
            task_id,
            "audit",
            "audit",
            correlation_id[:16],
            json.dumps(body),
            user_id,
            escalation_request_id,
        ),
    )
    await conn.commit()
    logger.info(
        "escalation_audit_written",
        escalation_event=event,
        escalation_request_id=escalation_request_id,
        correlation_id=correlation_id,
        user_id=user_id,
    )
    return invocation_id
