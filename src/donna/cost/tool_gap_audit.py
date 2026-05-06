"""Audit-log helper for the tool-gap lifecycle (slice 22).

Mirrors :mod:`donna.cost.escalation_audit`. Every state transition
(detected, filed, snoozed, owner-mismatch, filled, rejected) writes one
``invocation_log`` row with ``task_type='tool_gap_lifecycle'`` and a
JSON payload describing the event.

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

TOOL_GAP_TASK_TYPE = "tool_gap_lifecycle"

# Event names (also referenced in canonical spec §10.10).
EVENT_TOOL_GAP_DETECTED = "tool_gap_detected"
EVENT_TOOL_REQUEST_FILED = "tool_request_filed"
EVENT_TOOL_GAP_SNOOZED = "tool_gap_snoozed"
EVENT_TOOL_GAP_OWNER_MISMATCH = "tool_gap_owner_mismatch"
EVENT_TOOL_REQUEST_FILLED = "tool_request_filled"
EVENT_TOOL_REQUEST_REJECTED = "tool_request_rejected"


async def write_tool_gap_event(
    conn: aiosqlite.Connection,
    *,
    event: str,
    tool_request_id: int,
    user_id: str,
    payload: dict[str, Any],
    escalation_request_id: int | None = None,
    now: datetime | None = None,
) -> str:
    """Insert one ``tool_gap_lifecycle`` audit row.

    Args:
        conn: Shared aiosqlite connection.
        event: One of the ``EVENT_*`` constants.
        tool_request_id: Stamped into ``input_hash`` (truncated) so the
            row is queryable by gap id without a JSON scan.
        user_id: Owner of the gap.
        payload: Free-form event data merged with ``{"event": event,
            "tool_request_id": id}``.
        escalation_request_id: Optional FK when the event ties to a
            specific tool-build escalation (``tool_request_filed``,
            ``tool_request_filled``).
        now: Override timestamp (tests).

    Returns:
        The new ``invocation_log.id`` (UUIDv7 string).
    """
    body = {"event": event, "tool_request_id": tool_request_id, **payload}
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
            TOOL_GAP_TASK_TYPE,
            None,
            "audit",
            "audit",
            f"toolreq:{tool_request_id}"[:16],
            json.dumps(body),
            user_id,
            escalation_request_id,
        ),
    )
    await conn.commit()
    logger.info(
        "tool_gap_audit_written",
        tool_gap_event=event,
        tool_request_id=tool_request_id,
        user_id=user_id,
        escalation_request_id=escalation_request_id,
    )
    return invocation_id
