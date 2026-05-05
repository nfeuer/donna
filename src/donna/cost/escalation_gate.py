"""Estimate-driven gate for the over-budget decision tree (slice 17).

When a task's pre-flight ``estimate_usd`` exceeds either the daily
budget remaining or ``task_approval_threshold_usd``, the gate writes
an :class:`EscalationRequest` row, posts a Discord message with the
configured buttons, and awaits the user's resolution. ``Pause`` and
``Cancel`` are the only buttons rendered in slice 17; ``api_extended``,
``chat`` and ``claude_code`` modes ship in slices 18, 20 and 21.

This is *not* a replacement for :class:`donna.cost.budget.BudgetGuard`.
``BudgetGuard`` continues to be the post-hoc spend-vs-threshold backstop
that runs even when no estimate is available; the gate is the
estimate-aware path that gives the user agency.

Realizes docs/superpowers/specs/manual-escalation.md §4 and §6.1.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from typing import ClassVar, Literal

import structlog
import uuid6

from donna.config import ManualEscalationConfig
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import (
    EVENT_OFFERED,
    EVENT_RESOLVED,
    write_escalation_event,
)
from donna.cost.escalation_repository import (
    EscalationRepository,
    EscalationRequestRow,
)
from donna.cost.tracker import CostTracker

logger = structlog.get_logger()

# Internal escalation outcomes the gate can return. The set deliberately
# matches the canonical spec §4 even though slice 17 only renders two
# of these — slices 18/20/21 will return the others.
EscalationMode = Literal["pause", "cancel", "api_extended", "chat", "claude_code"]
ResolvedBy = Literal["user", "timeout"]

# Buttons rendered in slice 17. The other modes are intentionally
# deferred even when their config flags are on.
SLICE_17_RENDERED_MODES: tuple[str, ...] = ("pause", "cancel")


@dataclasses.dataclass(frozen=True)
class GateOutcome:
    """Result returned by :meth:`EscalationGate.fire_and_wait`."""

    fired: bool
    """Whether an escalation was offered (a row was created)."""
    mode: EscalationMode | None
    """Resolution mode. ``None`` when ``fired`` is False."""
    resolved_by: ResolvedBy | None
    """``user`` for a button click, ``timeout`` for the sweeper."""
    escalation_request_id: int | None
    """FK so callers can stamp resulting invocation_log rows."""
    correlation_id: str | None


# Type alias for the Discord delivery callback supplied by the bot
# wiring layer. Returns True if delivery succeeded.
DeliveryCallback = Callable[[EscalationRequestRow], Awaitable[bool]]


class EscalationGate:
    """Decides whether to escalate, fires the Discord view, awaits resolution."""

    # Class-level registry of correlation_id → asyncio.Event. The
    # delivery loop and the view's button handlers signal here when
    # they resolve a row so ``fire_and_wait`` can return.
    _events: ClassVar[dict[str, asyncio.Event]] = {}

    def __init__(
        self,
        *,
        repository: EscalationRepository,
        tracker: CostTracker,
        config: ManualEscalationConfig,
        daily_pause_threshold_usd: float,
        resolver: DashboardSettingResolver,
        deliver: DeliveryCallback,
    ) -> None:
        self._repo = repository
        self._tracker = tracker
        self._config = config
        self._daily_pause_threshold_usd = daily_pause_threshold_usd
        self._resolver = resolver
        self._deliver = deliver

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fire_and_wait(
        self,
        *,
        user_id: str,
        task_id: str | None,
        task_type: str,
        estimate_usd: float,
        priority: int = 2,
    ) -> GateOutcome:
        """Decide whether to escalate; if so, post the view and await.

        Returns a :class:`GateOutcome` describing what the caller should
        do next:
          * ``fired=False`` — caller proceeds normally; budget OK.
          * ``fired=True, mode='pause'`` — caller transitions task to
            ``paused`` and exits without spending.
          * ``fired=True, mode='cancel'`` — caller transitions task to
            ``cancelled`` and exits without spending.
          * Other modes are reserved for slices 18+.
        """
        if not await self._is_enabled():
            return GateOutcome(
                fired=False,
                mode=None,
                resolved_by=None,
                escalation_request_id=None,
                correlation_id=None,
            )

        daily_remaining = await self._daily_remaining(user_id)
        threshold = self._config.triggers.task_approval_threshold_usd
        if estimate_usd <= min(daily_remaining, threshold):
            return GateOutcome(
                fired=False,
                mode=None,
                resolved_by=None,
                escalation_request_id=None,
                correlation_id=None,
            )

        offered_modes = list(SLICE_17_RENDERED_MODES)
        correlation_id = str(uuid6.uuid7())
        row = await self._repo.create(
            user_id=user_id,
            correlation_id=correlation_id,
            task_id=task_id,
            task_type=task_type,
            estimate_usd=estimate_usd,
            daily_remaining_usd=daily_remaining,
            offered_modes=offered_modes,
            priority=priority,
        )

        await write_escalation_event(
            self._repo._conn,
            event=EVENT_OFFERED,
            escalation_request_id=row.id,
            correlation_id=correlation_id,
            user_id=user_id,
            task_id=task_id,
            payload={
                "task_type": task_type,
                "estimate_usd": estimate_usd,
                "daily_remaining_usd": daily_remaining,
                "modes": offered_modes,
                "priority": priority,
            },
        )

        event = asyncio.Event()
        EscalationGate._events[correlation_id] = event

        try:
            delivered = await self._deliver(row)
            if delivered:
                await self._repo.mark_delivery_attempt(
                    row.id, delivery_status="sent"
                )
            else:
                await self._repo.mark_delivery_attempt(
                    row.id, delivery_status="failed"
                )

            await event.wait()
            resolved = await self._repo.get(row.id)
            if resolved is None or resolved.resolution is None:
                # Defensive: the event was set but the row isn't
                # actually resolved. Treat as a pause so the caller
                # doesn't spend.
                logger.warning(
                    "escalation_event_set_without_resolution",
                    escalation_request_id=row.id,
                    correlation_id=correlation_id,
                )
                return GateOutcome(
                    fired=True,
                    mode="pause",
                    resolved_by="timeout",
                    escalation_request_id=row.id,
                    correlation_id=correlation_id,
                )
            return GateOutcome(
                fired=True,
                mode=_coerce_mode(resolved.resolution),
                resolved_by=_coerce_resolved_by(resolved.resolved_by),
                escalation_request_id=row.id,
                correlation_id=correlation_id,
            )
        finally:
            EscalationGate._events.pop(correlation_id, None)

    # ------------------------------------------------------------------
    # Hooks for the Discord view + delivery loop
    # ------------------------------------------------------------------

    @classmethod
    def signal_resolution(cls, correlation_id: str) -> None:
        """Wake any awaiter for ``correlation_id``.

        Called by the view's button handlers and by the timeout sweep
        in :mod:`donna.notifications.escalation_delivery_loop`.
        """
        event = cls._events.get(correlation_id)
        if event is not None:
            event.set()

    async def record_user_resolution(
        self,
        *,
        correlation_id: str,
        mode: EscalationMode,
        owner_user_id: str,
        task_id: str | None,
    ) -> bool:
        """Persist a user-driven resolution and write the audit entry.

        Returns True if this call mutated the row, False if it was
        already resolved (race with another button click or the
        timeout sweep).
        """
        row = await self._repo.get_by_correlation(correlation_id)
        if row is None:
            return False
        ok = await self._repo.resolve(
            row.id, resolution=mode, resolved_by="user"
        )
        if not ok:
            return False
        await write_escalation_event(
            self._repo._conn,
            event=EVENT_RESOLVED,
            escalation_request_id=row.id,
            correlation_id=correlation_id,
            user_id=owner_user_id,
            task_id=task_id,
            payload={"mode": mode, "resolved_by": "user"},
        )
        EscalationGate.signal_resolution(correlation_id)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _is_enabled(self) -> bool:
        """Resolve the master kill switch (dashboard → YAML)."""
        return await self._resolver.get(
            "manual_escalation.enabled", self._config.enabled
        )

    async def _daily_remaining(self, user_id: str) -> float:
        """Compute today's remaining budget envelope.

        Mirrors :class:`donna.cost.budget.BudgetGuard` exclusions so the
        gate's accounting matches the rest of the cost subsystem.
        """
        summary = await self._tracker.get_daily_cost(
            exclude_task_types=["external_llm_call", "escalation_lifecycle"]
        )
        remaining = self._daily_pause_threshold_usd - summary.total_usd
        return max(0.0, remaining)


def _coerce_mode(raw: str) -> EscalationMode:
    if raw not in {"pause", "cancel", "api_extended", "chat", "claude_code"}:
        # Defensive — DB shouldn't allow this.
        logger.warning("escalation_unknown_resolution", resolution=raw)
        return "pause"
    return raw  # type: ignore[return-value]


def _coerce_resolved_by(raw: str | None) -> ResolvedBy:
    if raw == "timeout":
        return "timeout"
    return "user"
