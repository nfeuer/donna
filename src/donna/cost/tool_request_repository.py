"""Async aiosqlite repository for ``tool_request`` (slice 22).

CRUD + dedup-on-open + snooze for the §7 tool-gap protocol's storage
layer. Mirrors the raw-aiosqlite pattern used by
:class:`donna.cost.escalation_repository.EscalationRepository` —
single shared connection, no SQLAlchemy ORM at runtime.

Realizes docs/superpowers/specs/manual-escalation.md §7, §8.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import structlog

from donna.cost.tool_gap import (
    DEFAULT_PRIORITY,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_OPEN,
    STATUS_REJECTED,
    ToolGap,
)

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class ToolRequestRow:
    """In-memory projection of a ``tool_request`` row.

    All datetimes are timezone-aware (UTC). Optional columns default
    to ``None`` so future migrations can extend without breaking
    callers.
    """

    id: int
    user_id: str
    tool_name: str
    proposed_signature: dict[str, Any] | None
    rationale: str | None
    blocking_capability_id: str | None
    priority: int
    status: str
    severity: str
    detection_point: str | None
    snoozed_until: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    resolved_at: datetime | None
    resolved_branch: str | None
    escalation_request_id: int | None
    last_pinged_at: datetime | None


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    # SQLite stores ISO strings. ``CURRENT_TIMESTAMP`` produces
    # ``YYYY-MM-DD HH:MM:SS`` (no T, no tz); ``datetime.isoformat()``
    # produces ``YYYY-MM-DDTHH:MM:SS+00:00``. Handle both.
    text = value.replace(" ", "T") if "T" not in value else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _row_to_request(
    cols: list[str], row: aiosqlite.Row | tuple[Any, ...]
) -> ToolRequestRow:
    data = dict(zip(cols, row, strict=False))
    proposed_raw = data.get("proposed_signature")
    proposed: dict[str, Any] | None = None
    if proposed_raw:
        try:
            proposed = json.loads(proposed_raw)
        except (TypeError, ValueError):
            proposed = None
    return ToolRequestRow(
        id=int(data["id"]),
        user_id=str(data["user_id"]),
        tool_name=str(data["tool_name"]),
        proposed_signature=proposed,
        rationale=data.get("rationale"),
        blocking_capability_id=data.get("blocking_capability_id"),
        priority=int(data.get("priority") or DEFAULT_PRIORITY),
        status=str(data.get("status") or STATUS_OPEN),
        severity=str(data.get("severity") or "speculative"),
        detection_point=data.get("detection_point"),
        snoozed_until=_parse_dt(data.get("snoozed_until")),
        first_seen_at=_parse_dt(data.get("first_seen_at")) or datetime.now(tz=UTC),
        last_seen_at=_parse_dt(data.get("last_seen_at")) or datetime.now(tz=UTC),
        created_at=_parse_dt(data.get("created_at")) or datetime.now(tz=UTC),
        resolved_at=_parse_dt(data.get("resolved_at")),
        resolved_branch=data.get("resolved_branch"),
        escalation_request_id=(
            int(data["escalation_request_id"])
            if data.get("escalation_request_id") is not None
            else None
        ),
        last_pinged_at=_parse_dt(data.get("last_pinged_at")),
    )


def _columns(cursor: aiosqlite.Cursor) -> list[str]:
    return [c[0] for c in (cursor.description or [])]


@dataclasses.dataclass(frozen=True)
class RecordResult:
    """Outcome of :meth:`ToolRequestRepository.record`.

    ``is_new`` discriminates a fresh insert (Discord ping is fresh
    notification material) from a dedup hit on an existing open row
    (rate-limit guards apply).
    """

    row: ToolRequestRow
    is_new: bool


class ToolRequestRepository:
    """CRUD for the ``tool_request`` table."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def record(
        self,
        gap: ToolGap,
        *,
        now: datetime | None = None,
    ) -> RecordResult:
        """Upsert a gap.

        If an open row exists for ``(user_id, tool_name)``: bump
        ``priority`` to ``max(existing, new)``, refresh ``rationale`` /
        ``severity`` / ``detection_point`` / ``last_seen_at``, **do not
        clear** ``snoozed_until`` (re-emission while snoozed is silent).

        Otherwise: insert a fresh row in ``status='open'``.
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        existing = await self.find_open(gap.user_id, gap.tool_name)
        if existing is not None:
            new_priority = max(existing.priority, gap.priority)
            # Promote severity if gap is more urgent than the open row.
            new_severity = (
                "high" if "high" in (existing.severity, gap.severity) else gap.severity
            )
            payload_json = (
                json.dumps(gap.proposed_signature)
                if gap.proposed_signature
                else (
                    json.dumps(existing.proposed_signature)
                    if existing.proposed_signature
                    else None
                )
            )
            await self._conn.execute(
                """
                UPDATE tool_request
                   SET priority = ?,
                       severity = ?,
                       rationale = ?,
                       detection_point = ?,
                       blocking_capability_id = COALESCE(?, blocking_capability_id),
                       proposed_signature = ?,
                       last_seen_at = ?
                 WHERE id = ?
                """,
                (
                    new_priority,
                    new_severity,
                    gap.rationale,
                    gap.detection_point,
                    gap.blocking_capability_id,
                    payload_json,
                    ts,
                    existing.id,
                ),
            )
            await self._conn.commit()
            row = await self.get(existing.id)
            assert row is not None
            return RecordResult(row=row, is_new=False)

        cursor = await self._conn.execute(
            """
            INSERT INTO tool_request (
                user_id, tool_name, proposed_signature, rationale,
                blocking_capability_id, priority, status, severity,
                detection_point, first_seen_at, last_seen_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gap.user_id,
                gap.tool_name,
                json.dumps(gap.proposed_signature) if gap.proposed_signature else None,
                gap.rationale,
                gap.blocking_capability_id,
                gap.priority,
                STATUS_OPEN,
                gap.severity,
                gap.detection_point,
                ts,
                ts,
                ts,
            ),
        )
        await self._conn.commit()
        new_id = cursor.lastrowid
        if new_id is None:
            raise RuntimeError("tool_request insert returned no lastrowid")
        row = await self.get(int(new_id))
        if row is None:
            raise RuntimeError(f"tool_request {new_id} disappeared after insert")
        logger.info(
            "tool_request_inserted",
            tool_request_id=row.id,
            tool_name=row.tool_name,
            user_id=row.user_id,
            severity=row.severity,
            detection_point=row.detection_point,
        )
        return RecordResult(row=row, is_new=True)

    async def snooze(
        self,
        request_id: int,
        *,
        seconds: int = 86400,
        now: datetime | None = None,
    ) -> bool:
        """Set ``snoozed_until = now + seconds`` if row is open.

        Returns True if the row was updated, False otherwise (already
        resolved / snoozed beyond requested deadline / unknown id).
        """
        base = now or datetime.now(tz=UTC)
        until = base + timedelta(seconds=seconds)
        cursor = await self._conn.execute(
            """
            UPDATE tool_request
               SET snoozed_until = ?, last_seen_at = ?
             WHERE id = ? AND status = ?
            """,
            (until.isoformat(), base.isoformat(), request_id, STATUS_OPEN),
        )
        await self._conn.commit()
        return (cursor.rowcount or 0) > 0

    async def mark_in_progress(
        self,
        request_id: int,
        *,
        escalation_request_id: int,
        now: datetime | None = None,
    ) -> bool:
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE tool_request
               SET status = ?, escalation_request_id = ?, last_seen_at = ?
             WHERE id = ? AND status = ?
            """,
            (STATUS_IN_PROGRESS, escalation_request_id, ts, request_id, STATUS_OPEN),
        )
        await self._conn.commit()
        return (cursor.rowcount or 0) > 0

    async def mark_completed(
        self,
        request_id: int,
        *,
        branch_name: str,
        now: datetime | None = None,
    ) -> bool:
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE tool_request
               SET status = ?, resolved_at = ?, resolved_branch = ?,
                   last_seen_at = ?
             WHERE id = ? AND status IN (?, ?)
            """,
            (
                STATUS_COMPLETED,
                ts,
                branch_name,
                ts,
                request_id,
                STATUS_OPEN,
                STATUS_IN_PROGRESS,
            ),
        )
        await self._conn.commit()
        return (cursor.rowcount or 0) > 0

    async def mark_rejected(
        self,
        request_id: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        ts = (now or datetime.now(tz=UTC)).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE tool_request
               SET status = ?, resolved_at = ?, last_seen_at = ?
             WHERE id = ? AND status IN (?, ?)
            """,
            (
                STATUS_REJECTED,
                ts,
                ts,
                request_id,
                STATUS_OPEN,
                STATUS_IN_PROGRESS,
            ),
        )
        await self._conn.commit()
        return (cursor.rowcount or 0) > 0

    async def mark_pinged(
        self,
        request_id: int,
        *,
        now: datetime | None = None,
    ) -> None:
        """Stamp ``last_pinged_at`` after a successful Discord post.

        Used by the surfacer to rate-limit re-pings on dedup hits."""
        ts = (now or datetime.now(tz=UTC)).isoformat()
        await self._conn.execute(
            "UPDATE tool_request SET last_pinged_at = ? WHERE id = ?",
            (ts, request_id),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, request_id: int) -> ToolRequestRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM tool_request WHERE id = ?", (request_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_request(_columns(cursor), row)

    async def find_open(
        self, user_id: str, tool_name: str
    ) -> ToolRequestRow | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM tool_request
             WHERE user_id = ? AND tool_name = ? AND status = ?
             LIMIT 1
            """,
            (user_id, tool_name, STATUS_OPEN),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_request(_columns(cursor), row)

    async def list_open_speculative(
        self,
        *,
        exclude_snoozed: bool = True,
        now: datetime | None = None,
    ) -> list[ToolRequestRow]:
        """Open + speculative + (optionally) not currently snoozed.

        Used by :class:`donna.notifications.digest.MorningDigest` to
        render the daily tool-gap aggregation. High-severity rows are
        excluded — they already pinged in real time.
        """
        sql = (
            "SELECT * FROM tool_request"
            " WHERE status = 'open' AND severity = 'speculative'"
        )
        params: list[Any] = []
        if exclude_snoozed:
            cutoff = (now or datetime.now(tz=UTC)).isoformat()
            sql += " AND (snoozed_until IS NULL OR snoozed_until < ?)"
            params.append(cutoff)
        sql += " ORDER BY priority DESC, first_seen_at ASC"
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, row) for row in rows]

    async def list_open_by_status(
        self,
        statuses: tuple[str, ...] = (STATUS_OPEN, STATUS_IN_PROGRESS),
    ) -> list[ToolRequestRow]:
        placeholders = ",".join("?" for _ in statuses)
        cursor = await self._conn.execute(
            f"SELECT * FROM tool_request WHERE status IN ({placeholders})"
            " ORDER BY priority DESC, first_seen_at ASC",
            statuses,
        )
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, row) for row in rows]

    async def list_completed_resolved_before(
        self,
        *,
        cutoff: datetime,
    ) -> list[ToolRequestRow]:
        """Return ``status='completed'`` rows resolved before ``cutoff``.

        Slice 24 (spec §10.5 row 1) feeds this into the
        ``RequiresRebuildNagger``: a tool that's been merged for
        longer than the nag threshold but hasn't appeared in the
        orchestrator's ``ToolRegistry`` since reboot still needs a
        rebuild. We sort by ``resolved_at`` ASC so the oldest stuck
        rows nag first.
        """
        cursor = await self._conn.execute(
            """
            SELECT * FROM tool_request
             WHERE status = ? AND resolved_at IS NOT NULL AND resolved_at < ?
             ORDER BY resolved_at ASC
            """,
            (STATUS_COMPLETED, cutoff.isoformat()),
        )
        rows = await cursor.fetchall()
        cols = _columns(cursor)
        return [_row_to_request(cols, row) for row in rows]
