"""Daily budget extension repository — slice 18.

Manages the ``daily_budget_extension`` table: granting one-shot spend
increases that raise today's effective API cap, voiding stale grants on
crash recovery, and computing the extension total used by BudgetGuard and
EscalationGate.

Realizes docs/superpowers/specs/manual-escalation.md §5.1, §8, §10.6.
"""

from __future__ import annotations

import calendar as _calendar
import dataclasses
from datetime import UTC, date, datetime
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class DailyBudgetExtensionRow:
    """In-memory projection of a ``daily_budget_extension`` row."""

    id: int
    user_id: str
    date: date
    amount_usd: float
    granted_at: datetime
    granted_by: str
    escalation_request_id: int | None
    voided: bool


class BudgetExtensionRepository:
    """CRUD for ``daily_budget_extension``.

    All mutations are designed to be idempotent. The unique index on
    ``(escalation_request_id, granted_by)`` (added in migration
    d0e1f2a3b4c5) is the DB-level enforcement; :meth:`grant` returns
    ``None`` on a duplicate rather than raising.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def grant(
        self,
        *,
        user_id: str,
        for_date: date,
        amount_usd: float,
        granted_by: str,
        escalation_request_id: int,
        now: datetime | None = None,
    ) -> DailyBudgetExtensionRow | None:
        """Insert a new extension grant.

        Idempotent: if a row already exists for the same
        ``(escalation_request_id, granted_by)`` pair (Discord retry), the
        existing row is returned unchanged and no duplicate is inserted.

        Args:
            user_id: Owner of the escalation.
            for_date: The calendar date the extension applies to.
            amount_usd: Grant amount; rounded up by the caller.
            granted_by: Discord user ID of the approver.
            escalation_request_id: FK into ``escalation_request``.
            now: Override timestamp (tests).

        Returns:
            The new or existing ``DailyBudgetExtensionRow``, or ``None``
            if a DB error prevents insertion (logged; never raises).
        """
        ts = (now or datetime.now(tz=UTC)).isoformat()
        try:
            cursor = await self._conn.execute(
                """
                INSERT INTO daily_budget_extension
                    (user_id, date, amount_usd, granted_at, granted_by,
                     escalation_request_id, voided)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT (escalation_request_id, granted_by) DO NOTHING
                """,
                (
                    user_id,
                    for_date.isoformat(),
                    amount_usd,
                    ts,
                    granted_by,
                    escalation_request_id,
                ),
            )
            await self._conn.commit()
        except Exception:
            logger.exception(
                "budget_extension_grant_failed",
                escalation_request_id=escalation_request_id,
                granted_by=granted_by,
            )
            return None

        if cursor.rowcount == 0:
            # Conflict: row already exists for this (escalation_request_id,
            # granted_by) pair. Look up and return the existing row.
            existing = await self._get_by_idempotency_key(
                escalation_request_id, granted_by
            )
            logger.info(
                "budget_extension_already_granted",
                escalation_request_id=escalation_request_id,
                granted_by=granted_by,
                existing_id=existing.id if existing else None,
            )
            return existing

        new_id = cursor.lastrowid
        if new_id is None:
            logger.error(
                "budget_extension_no_lastrowid",
                escalation_request_id=escalation_request_id,
            )
            return None

        row = await self._get(int(new_id))
        logger.info(
            "budget_extension_granted",
            extension_id=new_id,
            escalation_request_id=escalation_request_id,
            amount_usd=amount_usd,
            granted_by=granted_by,
        )
        return row

    async def get_daily_total(self, user_id: str, for_date: date) -> float:
        """Sum of non-voided extensions for ``user_id`` on ``for_date``."""
        cursor = await self._conn.execute(
            """
            SELECT COALESCE(SUM(amount_usd), 0.0)
            FROM daily_budget_extension
            WHERE user_id = ? AND date = ? AND voided = 0
            """,
            (user_id, for_date.isoformat()),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def get_monthly_total(
        self, user_id: str, year: int, month: int
    ) -> float:
        """Sum of non-voided extensions for ``user_id`` in the given month."""
        _, last_day = _calendar.monthrange(year, month)
        month_start = date(year, month, 1).isoformat()
        month_end = date(year, month, last_day).isoformat()
        cursor = await self._conn.execute(
            """
            SELECT COALESCE(SUM(amount_usd), 0.0)
            FROM daily_budget_extension
            WHERE user_id = ? AND date >= ? AND date <= ? AND voided = 0
            """,
            (user_id, month_start, month_end),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def void_by_escalation_request_id(
        self,
        escalation_request_id: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Set ``voided=True`` for all extensions linked to an escalation.

        Returns:
            True if at least one row was updated, False otherwise.
        """
        _ = now  # reserved for future audit timestamp; not stored on the row
        cursor = await self._conn.execute(
            """
            UPDATE daily_budget_extension
               SET voided = 1
             WHERE escalation_request_id = ? AND voided = 0
            """,
            (escalation_request_id,),
        )
        await self._conn.commit()
        return bool(cursor.rowcount > 0)

    async def find_stale_grants(self) -> list[int]:
        """Return escalation_request_ids with a granted-but-unrun extension.

        A grant is "stale" when a ``daily_budget_extension`` row exists for
        an ``api_extended`` resolution but no real (non-escalation_lifecycle)
        ``invocation_log`` row was ever written for that escalation. This
        indicates the orchestrator crashed after granting the extension but
        before the API call could run.

        Used by the crash-recovery scan at boot to void extensions that were
        never consumed, preventing phantom headroom from persisting across
        restarts.
        """
        cursor = await self._conn.execute(
            """
            SELECT dbe.escalation_request_id
              FROM daily_budget_extension dbe
              JOIN escalation_request er
                ON dbe.escalation_request_id = er.id
              LEFT JOIN invocation_log il
                ON il.escalation_request_id = er.id
               AND il.task_type != 'escalation_lifecycle'
             WHERE er.resolution = 'api_extended'
               AND dbe.voided = 0
               AND il.id IS NULL
             GROUP BY dbe.escalation_request_id
            """
        )
        rows = await cursor.fetchall()
        return [int(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, extension_id: int) -> DailyBudgetExtensionRow | None:
        cursor = await self._conn.execute(
            "SELECT * FROM daily_budget_extension WHERE id = ?", (extension_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_extension(_columns(cursor), row)

    async def _get_by_idempotency_key(
        self, escalation_request_id: int, granted_by: str
    ) -> DailyBudgetExtensionRow | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM daily_budget_extension
             WHERE escalation_request_id = ? AND granted_by = ?
            """,
            (escalation_request_id, granted_by),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_extension(_columns(cursor), row)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _columns(cursor: aiosqlite.Cursor) -> list[str]:
    return [d[0] for d in cursor.description]


def _row_to_extension(
    cols: list[str], row: tuple[Any, ...]
) -> DailyBudgetExtensionRow:
    record = dict(zip(cols, row, strict=True))
    raw_date = record["date"]
    parsed_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))

    raw_granted_at = record["granted_at"]
    if isinstance(raw_granted_at, datetime):
        parsed_granted_at = (
            raw_granted_at
            if raw_granted_at.tzinfo
            else raw_granted_at.replace(tzinfo=UTC)
        )
    else:
        dt = datetime.fromisoformat(str(raw_granted_at))
        parsed_granted_at = dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    return DailyBudgetExtensionRow(
        id=int(record["id"]),
        user_id=str(record["user_id"]),
        date=parsed_date,
        amount_usd=float(record["amount_usd"]),
        granted_at=parsed_granted_at,
        granted_by=str(record["granted_by"]),
        escalation_request_id=(
            int(record["escalation_request_id"])
            if record["escalation_request_id"] is not None
            else None
        ),
        voided=bool(record["voided"]),
    )
