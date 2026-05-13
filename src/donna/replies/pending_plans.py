"""Pending action plan persistence for the Universal Reply Handler.

Stores LLM-proposed action plans awaiting user confirmation. Plans
auto-expire after a configurable timeout.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
import uuid6

logger = structlog.get_logger()


class PendingPlans:
    """Manage pending action plans for threads.

    Args:
        conn: aiosqlite connection with pending_action_plan table.
        expiry_minutes: How long a plan stays pending before auto-expiring.
    """

    def __init__(self, conn: Any, expiry_minutes: int = 60) -> None:
        self._conn = conn
        self._expiry_minutes = expiry_minutes

    async def save(
        self,
        thread_id: str,
        actions: list[dict[str, Any]],
        reply_text: str,
    ) -> str:
        """Save a new pending plan. Cancels any existing pending plan on this thread."""
        await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'rejected' "
            "WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        )
        plan_id = str(uuid6.uuid7())
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(minutes=self._expiry_minutes)
        await self._conn.execute(
            "INSERT INTO pending_action_plan"
            " (id, thread_id, actions_json, reply_text, status,"
            " created_at, expires_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (
                plan_id, thread_id, json.dumps(actions),
                reply_text, now.isoformat(), expires_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return plan_id

    async def get_pending(self, thread_id: str) -> dict[str, Any] | None:
        """Return the pending plan for a thread, or None."""
        now = datetime.now(tz=UTC).isoformat()
        cursor = await self._conn.execute(
            "SELECT id, thread_id, actions_json, reply_text, status, created_at, expires_at "
            "FROM pending_action_plan "
            "WHERE thread_id = ? AND status = 'pending' AND expires_at > ? "
            "ORDER BY created_at DESC LIMIT 1",
            (thread_id, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "thread_id": row[1],
            "actions_json": row[2],
            "reply_text": row[3],
            "status": row[4],
            "created_at": row[5],
            "expires_at": row[6],
        }

    async def confirm(self, thread_id: str) -> dict[str, Any] | None:
        """Mark the pending plan as confirmed and return it."""
        pending = await self.get_pending(thread_id)
        if pending is None:
            return None
        await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'confirmed' WHERE id = ?",
            (pending["id"],),
        )
        await self._conn.commit()
        return pending

    async def reject(self, thread_id: str) -> None:
        """Mark the pending plan as rejected."""
        await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'rejected' "
            "WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        )
        await self._conn.commit()

    async def expire_stale(self) -> int:
        """Expire all pending plans past their deadline. Returns count expired."""
        now = datetime.now(tz=UTC).isoformat()
        cursor = await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at <= ?",
            (now,),
        )
        await self._conn.commit()
        count: int = cursor.rowcount
        if count:
            logger.info("pending_plans_expired", count=count)
        return count
