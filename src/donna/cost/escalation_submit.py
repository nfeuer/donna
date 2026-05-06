"""Channel-agnostic submit-flow for manual escalations (slice 21).

The HTTP route at :func:`donna.api.routes.admin_escalations.submit_escalation`
and the Discord slash command ``/donna submit`` (slice 21) both need to
run identical schema-validation, mode-mismatch, iteration-cap, and
concurrent-submission guards. Slice 19 had this logic inline in the
route; slice 21 extracts it here so both callers share one
implementation.

Typed exceptions let each channel translate to its own idiom (HTTP
status codes for the route, ephemeral Discord replies for the slash
command).

Realizes docs/superpowers/specs/manual-escalation.md §5.3 (claude_code
submission), §10.3 row 1 (empty-answer rejection — schema enforced),
§10.10 (``escalation_submitted`` audit).
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


# Spec §6.1 manual_iteration_limit default. Mirrors slice 19's
# ``_MANUAL_ITERATION_LIMIT``; slice 23 will source this from
# ``dashboard_setting`` / ``ManualEscalationConfig`` resolver. For now
# we keep the literal here so both callers reference the same constant.
MANUAL_ITERATION_LIMIT = 3

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "escalation_submission.json"
_SUBMISSION_SCHEMA: dict[str, Any] | None = None


def _load_submission_schema() -> dict[str, Any]:
    global _SUBMISSION_SCHEMA
    if _SUBMISSION_SCHEMA is None:
        with open(_SCHEMA_PATH) as f:
            _SUBMISSION_SCHEMA = json.load(f)
    return _SUBMISSION_SCHEMA


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class SubmitError(Exception):
    """Base class for any submit-flow failure."""


class SchemaValidationError(SubmitError):
    """Payload failed JSON-schema validation. ``message`` carries the cause."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(SubmitError):
    """No escalation_request row matches ``correlation_id``."""


class NotAwaitingSubmissionError(SubmitError):
    """Row is not in a state that accepts a submission (open/cancelled/etc)."""

    def __init__(self, status: str) -> None:
        super().__init__(f"row.status={status!r}")
        self.status = status


class ModeMismatchError(SubmitError):
    """Payload's ``mode`` does not match the row's selected mode."""

    def __init__(self, expected: str, submitted: str) -> None:
        super().__init__(f"expected={expected!r} submitted={submitted!r}")
        self.expected = expected
        self.submitted = submitted


class IterationCapReachedError(SubmitError):
    """The row has hit ``MANUAL_ITERATION_LIMIT`` resubmits already."""

    def __init__(self, iteration: int, limit: int = MANUAL_ITERATION_LIMIT) -> None:
        super().__init__(f"iteration={iteration} limit={limit}")
        self.iteration = iteration
        self.limit = limit


class ConcurrentSubmissionError(SubmitError):
    """Another submission won the race between SELECT and UPDATE."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SubmitResult:
    """Successful-submission projection returned to callers."""

    correlation_id: str
    status: str
    submitted_at: str
    iteration: int
    mode: str


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


async def submit_escalation_core(
    conn: aiosqlite.Connection,
    correlation_id: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> SubmitResult:
    """Validate payload, transition row resolved → submitted, write audit.

    Atomically transitions the escalation_request row from ``resolved``
    (or ``failed`` for re-submits) to ``submitted``. Iteration counter
    increments only on the ``failed → submitted`` path so a clean first
    submission stays at iteration=1.

    Args:
        conn: Shared aiosqlite connection.
        correlation_id: Row identifier.
        payload: Submit body (already JSON-decoded). Validated against
            ``schemas/escalation_submission.json`` here.
        now: Timestamp override (tests).

    Raises:
        SchemaValidationError: payload doesn't match the discriminated-union schema.
        NotFoundError: no row with that correlation_id.
        NotAwaitingSubmissionError: row.status is not in {resolved, failed}.
        ModeMismatchError: payload.mode != row.mode.
        IterationCapReachedError: row.status='failed' AND row.iteration >= cap.
        ConcurrentSubmissionError: WHERE clause matched zero rows on UPDATE.

    Returns:
        :class:`SubmitResult` with the post-update fields.
    """
    schema = _load_submission_schema()
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(exc.message) from exc

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
        raise NotFoundError(correlation_id)

    cols = [d[0] for d in cursor.description]
    record = dict(zip(cols, row, strict=True))

    if record["status"] not in ("resolved", "failed"):
        raise NotAwaitingSubmissionError(str(record["status"]))

    if record["mode"] is not None and record["mode"] != payload["mode"]:
        raise ModeMismatchError(
            expected=str(record["mode"]),
            submitted=str(payload["mode"]),
        )

    if (
        record["status"] == "failed"
        and int(record["iteration"]) >= MANUAL_ITERATION_LIMIT
    ):
        raise IterationCapReachedError(int(record["iteration"]))

    ts = (now or datetime.now(tz=UTC)).isoformat()
    branch = payload["branch"] if payload["mode"] == "claude_code" else None

    # The mode discriminator + status are folded into the WHERE so a
    # concurrent writer cannot slip between SELECT and UPDATE. The CASE
    # on iteration evaluates ``status`` BEFORE the SET (SQLite evaluates
    # all RHS expressions against pre-update values), so iteration only
    # increments on the failed → submitted transition.
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
        raise ConcurrentSubmissionError(correlation_id)
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
        now=now,
    )

    return SubmitResult(
        correlation_id=correlation_id,
        status="submitted",
        submitted_at=ts,
        iteration=iteration,
        mode=str(payload["mode"]),
    )
