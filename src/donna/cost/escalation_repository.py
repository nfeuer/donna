"""Async aiosqlite repository for slice 17 escalation tables.

Mirrors the raw-aiosqlite pattern used by ``donna.tasks.database.Database``:
the orchestrator's runtime path operates on a single shared aiosqlite
connection, not a SQLAlchemy async session. SQLAlchemy ORM mappings
in :mod:`donna.tasks.db_models` are present for migrations and
read-only inspection, not runtime queries.

Realizes data access for ``escalation_request`` and ``dashboard_setting``
as defined by docs/superpowers/specs/manual-escalation.md §8 (slice 17
scope). Resolution is intentionally idempotent so that a Discord button
click and the timeout sweeper can race for the same row without
double-resolving.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"

DELIVERY_PENDING = "pending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class EscalationRequestRow:
    """In-memory projection of an ``escalation_request`` row.

    Slice 19 added the workspace columns (``prompt_body``, ``summary``,
    ``mode``, ``prompt_path``, ``result``, ``validation_result``,
    ``branch_name``) to the table; slice 20 follow-up surfaces them on
    the dataclass so the delivery callback can read them through normal
    attribute access. Without these fields, ``getattr(row, "summary",
    None)`` in the cli_wiring delivery callback silently degraded to
    ``None``, which meant chat-mode notifications never shipped the
    Ollama-rendered summary or the workspace ``.md`` attachment in
    production.
    """

    id: int
    user_id: str
    correlation_id: str
    task_id: str | None
    task_type: str
    estimate_usd: float
    daily_remaining_usd: float
    offered_modes: list[str]
    resolution: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    iteration: int
    status: str
    created_at: datetime
    priority: int
    delivery_status: str | None
    delivery_attempts: int
    last_delivery_attempt_at: datetime | None
    # Slice 19 / 20 — workspace columns. Optional everywhere because
    # rows created before chat-mode landed (or non-chat rows) leave them
    # NULL.
    prompt_body: str | None = None
    summary: str | None = None
    mode: str | None = None
    prompt_path: str | None = None
    result: str | None = None
    validation_result: Any | None = None
    branch_name: str | None = None


class EscalationRepository:
    """CRUD for ``escalation_request`` and ``dashboard_setting`` tables."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # escalation_request
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        user_id: str,
        correlation_id: str,
        task_id: str | None,
        task_type: str,
        estimate_usd: float,
        daily_remaining_usd: float,
        offered_modes: Sequence[str],
        priority: int,
        now: datetime | None = None,
    ) -> EscalationRequestRow:
        """Insert a new escalation_request row in ``status='open'``."""
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            INSERT INTO escalation_request (
                user_id, correlation_id, task_id, task_type,
                estimate_usd, daily_remaining_usd, offered_modes,
                iteration, status, created_at, priority,
                delivery_status, delivery_attempts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0)
            """,
            (
                user_id,
                correlation_id,
                task_id,
                task_type,
                estimate_usd,
                daily_remaining_usd,
                json.dumps(list(offered_modes)),
                STATUS_OPEN,
                ts,
                priority,
                DELIVERY_PENDING,
            ),
        )
        await self._conn.commit()
        new_id = cursor.lastrowid
        if new_id is None:
            raise RuntimeError("escalation_request insert returned no lastrowid")
        row = await self.get(int(new_id))
        if row is None:
            raise RuntimeError(
                f"escalation_request {new_id} disappeared after insert"
            )
        return row

    async def get(self, request_id: int) -> EscalationRequestRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM escalation_request WHERE id = ?", (request_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_request(_columns(cursor), row)

    async def get_by_correlation(
        self, correlation_id: str
    ) -> EscalationRequestRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM escalation_request WHERE correlation_id = ?",
            (correlation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_request(_columns(cursor), row)

    async def resolve(
        self,
        request_id: int,
        *,
        resolution: str,
        resolved_by: str,
        now: datetime | None = None,
    ) -> bool:
        """Atomically resolve an open escalation.

        Returns True if this call mutated the row, False if it was
        already resolved (lost the race against another click or the
        timeout sweep).
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE escalation_request
               SET status = ?, resolution = ?, resolved_by = ?, resolved_at = ?
             WHERE id = ? AND status = ?
            """,
            (STATUS_RESOLVED, resolution, resolved_by, ts, request_id, STATUS_OPEN),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def mark_delivery_attempt(
        self,
        request_id: int,
        *,
        delivery_status: str,
        now: datetime | None = None,
    ) -> None:
        """Record a delivery attempt outcome for the retry loop."""
        ts = (now or datetime.now(tz=UTC)).isoformat()
        await self._conn.execute(
            """
            UPDATE escalation_request
               SET delivery_status = ?,
                   delivery_attempts = delivery_attempts + 1,
                   last_delivery_attempt_at = ?
             WHERE id = ?
            """,
            (delivery_status, ts, request_id),
        )
        await self._conn.commit()

    async def list_open_pending_delivery(self) -> list[EscalationRequestRow]:
        """Open rows whose Discord delivery hasn't succeeded yet."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM escalation_request
             WHERE status = ?
               AND (delivery_status IS NULL OR delivery_status IN (?, ?))
            """,
            (STATUS_OPEN, DELIVERY_PENDING, DELIVERY_FAILED),
        )
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, r) for r in rows]

    async def list_open_past_timeout(
        self,
        *,
        timeout_minutes: int,
        now: datetime | None = None,
    ) -> list[EscalationRequestRow]:
        """Open rows whose ``created_at`` is older than the timeout window."""
        cutoff = (now or datetime.now(tz=UTC)) - timedelta(minutes=timeout_minutes)
        cursor = await self._conn.execute(
            """
            SELECT * FROM escalation_request
             WHERE status = ?
               AND created_at <= ?
            """,
            (STATUS_OPEN, cutoff.isoformat()),
        )
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, r) for r in rows]

    # ------------------------------------------------------------------
    # dashboard_setting (read-only — write path lands in slice 23)
    # ------------------------------------------------------------------

    async def get_dashboard_setting(self, key: str) -> Any | None:
        cursor = await self._conn.execute(
            "SELECT value FROM dashboard_setting WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        raw = row[0]
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    async def upsert_dashboard_setting(
        self,
        key: str,
        value: Any,
        *,
        updated_by: str = "system",
        now: datetime | None = None,
    ) -> None:
        """Direct upsert. Slice 17 uses this in tests and for SQL-driven
        toggle flips during slice 18–22 development; the dashboard
        write-path UI ships in slice 23.
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        await self._conn.execute(
            """
            INSERT INTO dashboard_setting (key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (key, json.dumps(value), ts, updated_by),
        )
        await self._conn.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _columns(cursor: aiosqlite.Cursor) -> list[str]:
    return [d[0] for d in cursor.description]


def _row_to_request(
    cols: list[str], row: Sequence[Any]
) -> EscalationRequestRow:
    record = dict(zip(cols, row, strict=True))
    offered_modes_raw = record["offered_modes"]
    if isinstance(offered_modes_raw, str):
        offered_modes = list(json.loads(offered_modes_raw))
    else:
        offered_modes = list(offered_modes_raw or [])
    validation_result_raw = record.get("validation_result")
    validation_result: Any | None = None
    if validation_result_raw is not None:
        if isinstance(validation_result_raw, str):
            try:
                validation_result = json.loads(validation_result_raw)
            except (TypeError, ValueError):
                validation_result = {"raw": validation_result_raw}
        else:
            validation_result = validation_result_raw
    return EscalationRequestRow(
        id=int(record["id"]),
        user_id=str(record["user_id"]),
        correlation_id=str(record["correlation_id"]),
        task_id=record["task_id"],
        task_type=str(record["task_type"]),
        estimate_usd=float(record["estimate_usd"]),
        daily_remaining_usd=float(record["daily_remaining_usd"]),
        offered_modes=offered_modes,
        resolution=record["resolution"],
        resolved_by=record["resolved_by"],
        resolved_at=_parse_dt(record["resolved_at"]),
        iteration=int(record["iteration"]),
        status=str(record["status"]),
        created_at=_parse_dt(record["created_at"]) or datetime.now(tz=UTC),
        priority=int(record["priority"]),
        delivery_status=record["delivery_status"],
        delivery_attempts=int(record["delivery_attempts"]),
        last_delivery_attempt_at=_parse_dt(record["last_delivery_attempt_at"]),
        prompt_body=record.get("prompt_body"),
        summary=record.get("summary"),
        mode=record.get("mode"),
        prompt_path=record.get("prompt_path"),
        result=record.get("result"),
        validation_result=validation_result,
        branch_name=record.get("branch_name"),
    )


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
