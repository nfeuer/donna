"""Background coroutine for slice 17 escalation delivery + timeout sweep.

Mirrors the polling pattern of
:meth:`donna.notifications.escalation.EscalationManager.check_and_advance`
(`src/donna/notifications/escalation.py:158`). One coroutine per
process polls every 60 seconds, retries Discord delivery for open
escalations whose first post failed, and sweeps escalations that
have timed out without a click.

On timeout: the row is resolved with ``mode='pause'``,
``resolved_by='timeout'``; the underlying task transitions to
``paused``; if the task's priority is high enough (default ``≥ 4``,
configurable), the existing :class:`EscalationManager` is invoked at
SMS tier-2 to fan out an SMS nudge.

Realizes docs/superpowers/specs/manual-escalation.md §10.1 (Discord
channel failures) and §4 (timeout fall-through).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from donna.cost.escalation_audit import (
    EVENT_TIMED_OUT,
    write_escalation_event,
)
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import (
    DELIVERY_FAILED,
    DELIVERY_SENT,
    EscalationRepository,
    EscalationRequestRow,
)
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.notifications.escalation import EscalationManager
    from donna.tasks.database import Database

logger = structlog.get_logger()

DEFAULT_TICK_SECONDS = 60
"""How often the loop wakes. Spec §10.1: cron retries every 60s."""

SMS_PRIORITY_THRESHOLD = 4
"""Spec §4 — timed-out escalations only fan out via SMS at priority ≥ 4."""


# Callback signature — given an open row, attempt to (re-)post the
# Discord view and return True on success.
DeliveryCallback = Callable[[EscalationRequestRow], Awaitable[bool]]


class EscalationDeliveryLoop:
    """Polls open escalation_request rows and drives delivery/timeout."""

    def __init__(
        self,
        *,
        db: Database,
        repository: EscalationRepository,
        timeout_minutes: int,
        deliver: DeliveryCallback,
        sms_manager: EscalationManager | None = None,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
        sms_priority_threshold: int = SMS_PRIORITY_THRESHOLD,
    ) -> None:
        self._db = db
        self._repo = repository
        self._timeout_minutes = timeout_minutes
        self._deliver = deliver
        self._sms_manager = sms_manager
        self._tick_seconds = tick_seconds
        self._sms_priority_threshold = sms_priority_threshold
        # UTC date last seen by the daily-refresh sweep. ``None`` until
        # the first tick so we don't fire a refresh at boot.
        self._last_refresh_date: object | None = None

    async def run(self) -> None:
        """Background entrypoint — schedule from server.run_server()."""
        logger.info(
            "escalation_delivery_loop_started",
            tick_seconds=self._tick_seconds,
            timeout_minutes=self._timeout_minutes,
        )
        while True:
            try:
                await self.tick_once()
            except Exception:
                logger.exception("escalation_delivery_loop_tick_failed")
            await asyncio.sleep(self._tick_seconds)

    async def tick_once(self, *, now: datetime | None = None) -> None:
        """One delivery + timeout pass. Exposed for tests."""
        ts = now or datetime.now(tz=UTC)
        await self._retry_pending_deliveries(now=ts)
        await self._sweep_timeouts(now=ts)
        await self._maybe_daily_refresh(now=ts)

    # ------------------------------------------------------------------
    # Delivery retry
    # ------------------------------------------------------------------

    async def _retry_pending_deliveries(self, *, now: datetime) -> None:
        rows = await self._repo.list_open_pending_delivery()
        for row in rows:
            # If the row's age exceeds the timeout window, skip — the
            # timeout sweeper will handle it shortly.
            age_minutes = (now - row.created_at).total_seconds() / 60.0
            if age_minutes >= self._timeout_minutes:
                continue
            try:
                ok = await self._deliver(row)
            except Exception:
                logger.exception(
                    "escalation_delivery_retry_raised",
                    correlation_id=row.correlation_id,
                )
                ok = False
            await self._repo.mark_delivery_attempt(
                row.id,
                delivery_status=DELIVERY_SENT if ok else DELIVERY_FAILED,
                now=now,
            )
            logger.info(
                "escalation_delivery_retry",
                correlation_id=row.correlation_id,
                delivered=ok,
                attempts=row.delivery_attempts + 1,
            )

    # ------------------------------------------------------------------
    # Timeout sweep
    # ------------------------------------------------------------------

    async def _sweep_timeouts(self, *, now: datetime) -> None:
        rows = await self._repo.list_open_past_timeout(
            timeout_minutes=self._timeout_minutes, now=now
        )
        for row in rows:
            mutated = await self._repo.resolve(
                row.id,
                resolution="pause",
                resolved_by="timeout",
                now=now,
            )
            if not mutated:
                continue
            await write_escalation_event(
                self._repo._conn,
                event=EVENT_TIMED_OUT,
                escalation_request_id=row.id,
                correlation_id=row.correlation_id,
                user_id=row.user_id,
                task_id=row.task_id,
                payload={
                    "task_type": row.task_type,
                    "estimate_usd": row.estimate_usd,
                    "priority": row.priority,
                    "delivery_attempts": row.delivery_attempts,
                },
                now=now,
            )
            EscalationGate.signal_resolution(row.correlation_id)
            await self._transition_task_to_paused(row)
            await self._maybe_fan_out_sms(row)
            logger.info(
                "escalation_timed_out",
                correlation_id=row.correlation_id,
                escalation_request_id=row.id,
                priority=row.priority,
            )

    async def _transition_task_to_paused(self, row: EscalationRequestRow) -> None:
        if row.task_id is None:
            return
        try:
            await self._db.transition_task_state(row.task_id, TaskStatus.PAUSED)
        except Exception:
            # Log but don't stop the sweep — other rows still need handling.
            logger.exception(
                "escalation_pause_transition_failed",
                task_id=row.task_id,
                correlation_id=row.correlation_id,
            )

    # ------------------------------------------------------------------
    # Daily budget refresh — paused → backlog at UTC rollover.
    # ------------------------------------------------------------------

    async def _maybe_daily_refresh(self, *, now: datetime) -> None:
        today = now.date()
        if self._last_refresh_date is None:
            self._last_refresh_date = today
            return
        if self._last_refresh_date == today:
            return
        # New UTC day — reopen tasks that were paused by the gate so the
        # scheduler can reconsider them with the refreshed envelope.
        await self._refresh_paused_tasks()
        self._last_refresh_date = today

    async def _refresh_paused_tasks(self) -> None:
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT id FROM tasks WHERE status = ?", (TaskStatus.PAUSED.value,)
        )
        rows = list(await cursor.fetchall())
        for (task_id,) in rows:
            try:
                await self._db.transition_task_state(task_id, TaskStatus.BACKLOG)
            except Exception:
                logger.exception(
                    "escalation_daily_refresh_transition_failed",
                    task_id=task_id,
                )
        logger.info(
            "escalation_daily_refresh_completed", reopened_count=len(rows)
        )

    async def _maybe_fan_out_sms(self, row: EscalationRequestRow) -> None:
        if self._sms_manager is None:
            return
        if row.priority < self._sms_priority_threshold:
            return
        if row.task_id is None:
            return
        try:
            await self._sms_manager.escalate(
                task_id=row.task_id,
                task_title=f"Over-budget: {row.task_type}",
                nudge_text=(
                    f"Over-budget task '{row.task_type}' was paused — "
                    "needs your attention."
                ),
                priority=row.priority,
                start_at_tier=2,
            )
        except Exception:
            logger.exception(
                "escalation_sms_fanout_failed",
                correlation_id=row.correlation_id,
            )
