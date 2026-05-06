"""Shared submission logic for chat-mode + claude_code-mode escalations.

The HTTP endpoint at ``POST /admin/escalations/{cid}/submit`` (slice 19)
and the Discord ``/donna submit`` slash command (slice 20) both need to
apply the *same* validation, optimistic-lock update, and audit-log write.
This module owns that path so the two entry points cannot drift.

Realizes ``docs/superpowers/specs/manual-escalation.md`` §5.2 / §5.3 /
§10.3 / §10.10.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import jsonschema
import structlog

from donna.cost.escalation_audit import write_escalation_event

logger = structlog.get_logger()


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "schemas"
    / "escalation_submission.json"
)
_SUBMISSION_SCHEMA: dict[str, Any] | None = None


def _load_submission_schema() -> dict[str, Any]:
    global _SUBMISSION_SCHEMA
    if _SUBMISSION_SCHEMA is None:
        with open(_SCHEMA_PATH) as f:
            _SUBMISSION_SCHEMA = json.load(f)
    return _SUBMISSION_SCHEMA


# Spec §6.1 ``manual_iteration_limit`` default. Mirrors the constant the
# admin_escalations route used pre-slice-20; lifted here so both the HTTP
# and slash-command paths share the same cap when the dashboard runtime
# override is absent.
DEFAULT_MANUAL_ITERATION_LIMIT = 3


class SubmissionError(Exception):
    """Raised when a submission cannot proceed.

    Carries a stable ``code`` so HTTP and Discord callers can map the
    failure to the right user-facing message. ``status_code`` is the
    HTTP status the REST handler should emit; the slash-command path
    ignores it and uses ``code`` alone.

    The set of codes lines up with the strings the slice 19 endpoint
    already returned so existing dashboard JSON/Discord ephemeral
    messages keep working unchanged.
    """

    def __init__(
        self,
        *,
        code: str,
        status_code: int,
        message: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message
        self.extras = extras or {}
        super().__init__(message or code)


@dataclasses.dataclass(frozen=True)
class SubmissionResult:
    """Successful return from :func:`apply_submission`."""

    correlation_id: str
    status: str
    submitted_at: str
    iteration: int
    mode: str


async def apply_submission(
    *,
    conn: aiosqlite.Connection,
    correlation_id: str,
    payload: dict[str, Any],
    iteration_limit: int = DEFAULT_MANUAL_ITERATION_LIMIT,
    now: datetime | None = None,
) -> SubmissionResult:
    """Apply a submission payload to an escalation_request row.

    Args:
        conn: Shared aiosqlite connection.
        correlation_id: The escalation's UUIDv7 identifier.
        payload: Already-deserialized submission JSON. Validated here
            against ``schemas/escalation_submission.json``.
        iteration_limit: Cap on iteration count (spec §6.1). Defaults to
            ``DEFAULT_MANUAL_ITERATION_LIMIT``.
        now: Override timestamp (tests).

    Returns:
        :class:`SubmissionResult` describing the post-update state.

    Raises:
        :class:`SubmissionError`: Validation, lookup, or optimistic-lock
            failure. ``code`` and ``status_code`` describe the cause.
    """
    schema = _load_submission_schema()
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise SubmissionError(
            code="schema_validation_failed",
            status_code=400,
            message=exc.message,
        ) from exc

    cursor = await conn.execute(
        """
        SELECT id, user_id, task_id, status, mode, iteration
          FROM escalation_request
         WHERE correlation_id = ?
        """,
        (correlation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise SubmissionError(
            code="not_found",
            status_code=404,
        )
    cols = [d[0] for d in cursor.description]
    record = dict(zip(cols, row, strict=True))

    if record["status"] not in ("resolved", "failed"):
        raise SubmissionError(
            code="not_awaiting_submission",
            status_code=409,
            extras={"status": record["status"]},
        )
    if record["mode"] is not None and record["mode"] != payload["mode"]:
        raise SubmissionError(
            code="mode_mismatch",
            status_code=409,
            extras={
                "expected_mode": record["mode"],
                "submitted_mode": payload["mode"],
            },
        )
    if (
        record["status"] == "failed"
        and int(record["iteration"]) >= iteration_limit
    ):
        raise SubmissionError(
            code="iteration_cap_reached",
            status_code=409,
            extras={
                "iteration": int(record["iteration"]),
                "limit": iteration_limit,
            },
        )

    moment = now or datetime.now(tz=UTC)
    ts = moment.isoformat()
    branch = payload["branch"] if payload["mode"] == "claude_code" else None

    update_cursor = await conn.execute(
        """
        UPDATE escalation_request
           SET status = 'submitted',
               submitted_at = ?,
               result = ?,
               branch_name = COALESCE(?, branch_name),
               iteration = iteration + CASE WHEN status = 'failed' THEN 1 ELSE 0 END,
               mode = COALESCE(mode, ?)
         WHERE correlation_id = ?
           AND status IN ('resolved', 'failed')
           AND (mode IS NULL OR mode = ?)
        """,
        (
            ts,
            json.dumps(payload),
            branch,
            payload["mode"],
            correlation_id,
            payload["mode"],
        ),
    )
    if update_cursor.rowcount == 0:
        raise SubmissionError(
            code="concurrent_submission",
            status_code=409,
        )
    await conn.commit()

    cursor = await conn.execute(
        "SELECT iteration FROM escalation_request WHERE correlation_id = ?",
        (correlation_id,),
    )
    iteration_row = await cursor.fetchone()
    iteration = int(iteration_row[0]) if iteration_row else 0

    await write_escalation_event(
        conn,
        event="escalation_submitted",
        escalation_request_id=int(record["id"]),
        correlation_id=correlation_id,
        user_id=str(record["user_id"]),
        task_id=record["task_id"],
        payload={
            "mode": payload["mode"],
            "branch": branch,
            "iteration": iteration,
        },
        now=moment,
    )

    logger.info(
        "escalation_submission_applied",
        correlation_id=correlation_id,
        mode=payload["mode"],
        iteration=iteration,
        branch=branch,
    )
    return SubmissionResult(
        correlation_id=correlation_id,
        status="submitted",
        submitted_at=ts,
        iteration=iteration,
        mode=payload["mode"],
    )


__all__ = [
    "DEFAULT_MANUAL_ITERATION_LIMIT",
    "SubmissionError",
    "SubmissionResult",
    "apply_submission",
]
