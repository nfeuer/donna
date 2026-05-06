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
    # Slice 21 additions — see docs/superpowers/specs/manual-escalation.md §5.3
    target_paths: dict[str, str] | None = None
    originating_entity_type: str | None = None
    originating_entity_id: str | None = None
    base_sha: str | None = None
    human_review: bool = False
    merged_at: datetime | None = None
    submitted_payload: dict[str, Any] | None = None
    """Decoded ``escalation_request.result`` JSON. Slice 21 reads
    ``sha`` from it for force-push protection (§10.3 row 4)."""


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
        originating_entity: tuple[str, str] | None = None,
        target_paths: dict[str, str] | None = None,
        base_sha: str | None = None,
        now: datetime | None = None,
    ) -> EscalationRequestRow:
        """Insert a new escalation_request row in ``status='open'``.

        ``originating_entity`` (slice 21) carries the FK pair pointing at
        the row that triggered the escalation — e.g.
        ``('skill_candidate_report', candidate.id)`` for skill_auto_draft.
        Required so the diff validator can render ``{name}``-substituted
        target_paths globs without inferring identity from ``task_id``
        (which is NULL for these task types).

        ``target_paths`` snapshots the rendered scope at gate-fire time
        so subsequent config changes don't retroactively widen scope.

        ``base_sha`` pins the worktree against a specific main SHA per
        spec §5.3 (worktree drift mitigation).
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        ent_type = originating_entity[0] if originating_entity else None
        ent_id = originating_entity[1] if originating_entity else None
        target_paths_json = json.dumps(target_paths) if target_paths else None
        cursor = await self._conn.execute(
            """
            INSERT INTO escalation_request (
                user_id, correlation_id, task_id, task_type,
                estimate_usd, daily_remaining_usd, offered_modes,
                iteration, status, created_at, priority,
                delivery_status, delivery_attempts,
                originating_entity_type, originating_entity_id,
                target_paths, base_sha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0, ?, ?, ?, ?)
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
                ent_type,
                ent_id,
                target_paths_json,
                base_sha,
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
    # dashboard_setting — read + lock-aware write (slice 23)
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

    async def get_dashboard_setting_row(
        self, key: str
    ) -> tuple[Any, str, str] | None:
        """Return ``(value, updated_at, updated_by)`` for ``key`` if present.

        Slice 23 needs the timestamp + actor to seed the optimistic-lock
        UI and surface "last changed by" provenance. Returning ``None``
        means no override exists for the key.
        """
        cursor = await self._conn.execute(
            "SELECT value, updated_at, updated_by FROM dashboard_setting WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        raw_value, updated_at, updated_by = row[0], row[1], row[2]
        value: Any = (
            json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        )
        return value, str(updated_at), str(updated_by)

    async def list_dashboard_settings(
        self, *, prefix: str | None = None
    ) -> list[tuple[str, Any, str, str]]:
        """Return every override row, optionally filtered by ``prefix``.

        Powers the slice 23 ``GET /admin/escalation-settings`` aggregation:
        we want the entire override snapshot in a single query so the UI
        can render side-by-side YAML defaults + dashboard overrides.
        """
        if prefix is not None:
            cursor = await self._conn.execute(
                "SELECT key, value, updated_at, updated_by "
                "FROM dashboard_setting WHERE key LIKE ?",
                (prefix + "%",),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT key, value, updated_at, updated_by FROM dashboard_setting"
            )
        out: list[tuple[str, Any, str, str]] = []
        for r in await cursor.fetchall():
            raw_value = r[1]
            value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            out.append((str(r[0]), value, str(r[2]), str(r[3])))
        return out

    # ------------------------------------------------------------------
    # Slice 21 — claude_code poller helpers
    # ------------------------------------------------------------------

    async def list_submitted_claude_code(self) -> list[EscalationRequestRow]:
        """Rows the claude_code poller should pick up.

        Selects ``mode='claude_code' AND status='submitted'`` — the
        narrow contract the slice 21 poller operates on. Slice 19's
        submit endpoint is the only writer that produces this state.
        """
        cursor = await self._conn.execute(
            """
            SELECT * FROM escalation_request
             WHERE mode = ? AND status = ?
             ORDER BY submitted_at ASC
            """,
            ("claude_code", "submitted"),
        )
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, r) for r in rows]

    async def list_failed_at_iteration_cap(
        self, *, manual_iteration_limit: int
    ) -> list[EscalationRequestRow]:
        """Rows that hit the iteration cap and need human review routing."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM escalation_request
             WHERE mode = ?
               AND status = ?
               AND iteration >= ?
               AND human_review = 0
            """,
            ("claude_code", "failed", manual_iteration_limit),
        )
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, r) for r in rows]

    async def find_open_for_originating_entity(
        self,
        *,
        user_id: str,
        entity_type: str,
        entity_id: str,
    ) -> EscalationRequestRow | None:
        """Return the open/in-flight claude_code row for the given entity.

        Used by the gate to de-dup escalations: if a previous
        ``claude_code`` run for the same skill/candidate is still
        in-flight (open / resolved / submitted / failed-but-under-cap),
        the gate re-delivers the existing notification instead of
        opening a parallel branch race.

        Slice 24 (spec §10.9 row 1) made ``user_id`` required:
        without it the dedup query was cross-tenant and a tool gap on
        user B's branch would mask user A's open escalation. Both call
        sites in :class:`EscalationGate` already had the owner in scope.

        Returns the most-recent matching row if any.
        """
        cursor = await self._conn.execute(
            """
            SELECT * FROM escalation_request
             WHERE user_id = ?
               AND originating_entity_type = ?
               AND originating_entity_id = ?
               AND status IN ('open', 'resolved', 'submitted', 'failed')
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (user_id, entity_type, entity_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_request(_columns(cursor), row)

    async def mark_validated(
        self,
        request_id: int,
        *,
        validation_result: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        """Transition ``submitted → validated`` and record the result blob."""
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE escalation_request
               SET status = 'validated',
                   validated_at = ?,
                   validation_result = ?
             WHERE id = ?
               AND status = 'submitted'
            """,
            (ts, json.dumps(validation_result), request_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def mark_failed(
        self,
        request_id: int,
        *,
        validation_result: dict[str, Any],
        human_review: bool = False,
        now: datetime | None = None,
    ) -> bool:
        """Transition ``submitted → failed`` and record the result blob.

        ``human_review=True`` is set by the iteration_cap_sweep when
        re-iteration is no longer available.
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE escalation_request
               SET status = 'failed',
                   validation_result = ?,
                   human_review = CASE WHEN ? THEN 1 ELSE human_review END,
                   resolved_at = COALESCE(resolved_at, ?)
             WHERE id = ?
               AND status = 'submitted'
            """,
            (
                json.dumps(validation_result),
                1 if human_review else 0,
                ts,
                request_id,
            ),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def mark_iteration_cap_reached(
        self,
        request_id: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Cancel a row that hit the iteration cap, flag for human review.

        Spec §10.4 row 2: at iteration cap, auto-cancel and route to a
        human review surface. We cancel the row (status='cancelled') and
        set human_review=1 so the dashboard list view can highlight it.
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE escalation_request
               SET status = 'cancelled',
                   human_review = 1,
                   resolved_at = COALESCE(resolved_at, ?)
             WHERE id = ?
               AND mode = 'claude_code'
               AND status = 'failed'
               AND human_review = 0
            """,
            (ts, request_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def set_manual_handoff(
        self,
        request_id: int,
        *,
        mode: str,
        prompt_path: str,
        prompt_body: str,
    ) -> bool:
        """Persist the rendered spec + selected mode on a row.

        Called by ``EscalationGate.record_manual_handoff`` BEFORE the
        resolution event fires, so the dashboard can render the spec
        the moment the user follows the Discord link.
        """
        cursor = await self._conn.execute(
            """
            UPDATE escalation_request
               SET mode = ?,
                   prompt_path = ?,
                   prompt_body = ?
             WHERE id = ?
               AND status = ?
            """,
            (mode, prompt_path, prompt_body, request_id, STATUS_OPEN),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def upsert_dashboard_setting(
        self,
        key: str,
        value: Any,
        *,
        updated_by: str = "system",
        now: datetime | None = None,
    ) -> str:
        """Direct upsert. Slice 17 uses this in tests and for SQL-driven
        toggle flips during slice 18–22 development; the dashboard
        write-path UI ships in slice 23 and prefers
        :meth:`set_dashboard_setting_with_lock`. Returns the new
        ``updated_at`` ISO-8601 string so callers can echo it back.
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
        return ts

    async def set_dashboard_setting_with_lock(
        self,
        key: str,
        value: Any,
        *,
        expected_updated_at: str | None,
        updated_by: str,
        now: datetime | None = None,
    ) -> tuple[bool, Any, str, str]:
        """Optimistic-lock write for slice 23 (spec §10.7 row 1).

        Semantics:
        - ``expected_updated_at is None`` means "the client believed the
          row did not exist". The write succeeds via ``INSERT``; an
          existing row makes ``INSERT`` raise ``IntegrityError`` which
          we translate to a conflict.
        - Otherwise the write is a conditional ``UPDATE ... WHERE
          updated_at = ?``. SQLite's UPDATE is atomic, so this single
          statement IS the lock — no explicit BEGIN required (the
          codebase keeps sqlite3's default deferred isolation, where
          ``BEGIN IMMEDIATE`` would error with "cannot start a
          transaction within a transaction" any time a prior implicit
          tx is in flight).
        - On conflict, returns ``(False, current_value, current_updated_at,
          current_updated_by)`` so the caller can surface the live state
          in a 409 response. If the row vanished entirely, returns
          ``(False, None, "", "")``.
        - On success, returns ``(True, value, new_updated_at, updated_by)``.
        """
        import sqlite3 as _sqlite3

        ts = (now or datetime.now(tz=UTC)).isoformat()

        if expected_updated_at is None:
            # First-write contract. PRIMARY KEY collision = conflict.
            try:
                await self._conn.execute(
                    """
                    INSERT INTO dashboard_setting
                        (key, value, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key, json.dumps(value), ts, updated_by),
                )
                await self._conn.commit()
                return True, value, ts, updated_by
            except _sqlite3.IntegrityError:
                # Row already exists; surface its state so the client
                # can re-render with the live value.
                current = await self.get_dashboard_setting_row(key)
                if current is None:
                    return False, None, "", ""
                return False, current[0], current[1], current[2]

        # Conditional UPDATE — the WHERE is the optimistic lock.
        cursor = await self._conn.execute(
            """
            UPDATE dashboard_setting
               SET value = ?, updated_at = ?, updated_by = ?
             WHERE key = ?
               AND updated_at = ?
            """,
            (json.dumps(value), ts, updated_by, key, expected_updated_at),
        )
        await self._conn.commit()
        if cursor.rowcount > 0:
            return True, value, ts, updated_by

        # The UPDATE matched zero rows: either the row vanished or the
        # token was stale. Read the live state for the conflict body.
        current = await self.get_dashboard_setting_row(key)
        if current is None:
            return False, None, "", ""
        return False, current[0], current[1], current[2]


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
    target_paths_raw = record.get("target_paths")
    target_paths: dict[str, str] | None
    if isinstance(target_paths_raw, str) and target_paths_raw:
        try:
            target_paths = json.loads(target_paths_raw)
        except (TypeError, ValueError):
            target_paths = None
    elif isinstance(target_paths_raw, dict):
        target_paths = dict(target_paths_raw)
    else:
        target_paths = None

    submitted_payload_raw = record.get("result")
    submitted_payload: dict[str, Any] | None
    if isinstance(submitted_payload_raw, str) and submitted_payload_raw:
        try:
            submitted_payload = json.loads(submitted_payload_raw)
        except (TypeError, ValueError):
            submitted_payload = None
    elif isinstance(submitted_payload_raw, dict):
        submitted_payload = dict(submitted_payload_raw)
    else:
        submitted_payload = None

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
        target_paths=target_paths,
        originating_entity_type=record.get("originating_entity_type"),
        originating_entity_id=record.get("originating_entity_id"),
        base_sha=record.get("base_sha"),
        human_review=bool(record.get("human_review", 0) or 0),
        merged_at=_parse_dt(record.get("merged_at")),
        submitted_payload=submitted_payload,
    )


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
