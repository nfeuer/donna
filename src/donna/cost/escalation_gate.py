"""Estimate-driven gate for the over-budget decision tree (slice 17/18).

When a task's pre-flight ``estimate_usd`` exceeds either the daily
budget remaining or ``task_approval_threshold_usd``, the gate writes
an :class:`EscalationRequest` row, posts a Discord message with the
configured buttons, and awaits the user's resolution.

Slice 17 shipped Pause + Cancel. Slice 18 adds ``api_extended``:
``[Approve $X extension]`` button, idempotent grant via
:class:`~donna.cost.budget_extension.BudgetExtensionRepository`,
hard daily and monthly ceilings, and the ``extension_amount_usd``
field on :class:`GateOutcome` for token-limit enforcement.

This is *not* a replacement for :class:`donna.cost.budget.BudgetGuard`.
``BudgetGuard`` continues to be the post-hoc spend-vs-threshold backstop
that runs even when no estimate is available; the gate is the
estimate-aware path that gives the user agency.

Realizes docs/superpowers/specs/manual-escalation.md §4, §5.1, §6.1,
§10.6.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, ClassVar, Literal

import structlog
import uuid6

from donna.config import ManualEscalationConfig, TaskTypesConfig
from donna.cost.budget_extension import BudgetExtensionRepository, DailyBudgetExtensionRow
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import (
    EVENT_EXTENSION_GRANTED,
    EVENT_OFFERED,
    EVENT_RESOLVED,
    write_escalation_event,
)
from donna.cost.escalation_repository import (
    EscalationRepository,
    EscalationRequestRow,
)
from donna.cost.tracker import CostTracker

if TYPE_CHECKING:
    from donna.cost.escalation_chat_prompt import ChatPromptBuilder

logger = structlog.get_logger()


# Internal escalation outcomes the gate can return.
EscalationMode = Literal["pause", "cancel", "api_extended", "chat", "claude_code"]
ResolvedBy = Literal["user", "timeout"]


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
    extension_amount_usd: float | None = None
    """Granted extension amount when ``mode='api_extended'``.

    Callers use this to derive the ``max_tokens`` hard cap so actual
    spend cannot exceed the approved extension (§10.6 row 1).
    """


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
        extension_repo: BudgetExtensionRepository,
        task_types_config: TaskTypesConfig | None = None,
        chat_prompt_builder: ChatPromptBuilder | None = None,
    ) -> None:
        self._repo = repository
        self._tracker = tracker
        self._config = config
        self._daily_pause_threshold_usd = daily_pause_threshold_usd
        self._resolver = resolver
        self._deliver = deliver
        self._extension_repo = extension_repo
        # Slice 20: per-task-type manual mode resolution + chat prompt
        # rendering. Both are optional so existing test fixtures + boots
        # without a Discord bot continue to assemble a gate that only
        # offers Pause / Cancel.
        self._task_types_config = task_types_config
        self._chat_prompt_builder = chat_prompt_builder

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
        original_prompt: str | None = None,
    ) -> GateOutcome:
        """Decide whether to escalate; if so, post the view and await.

        Returns a :class:`GateOutcome` describing what the caller should
        do next:
          * ``fired=False`` — caller proceeds normally; budget OK.
          * ``fired=True, mode='pause'`` — caller transitions task to
            ``paused`` and exits without spending.
          * ``fired=True, mode='cancel'`` — caller transitions task to
            ``cancelled`` and exits without spending.
          * ``fired=True, mode='api_extended'`` — extension was granted;
            caller proceeds with the API call. ``extension_amount_usd``
            is set for token-limit enforcement.
          * ``fired=True, mode='chat'`` — slice 20 manual handoff. Caller
            should treat the task as parked; the chat-mode ingestion
            poller transitions it to ``done`` once the user submits an
            answer through the dashboard or ``/donna submit``.

        ``original_prompt`` is the fully-rendered prompt the caller would
        otherwise have sent to the API. Slice 20 uses it (with the chat
        prompt builder) to produce the prompt body the user pastes into
        Claude. Without it, chat mode cannot be offered for this call —
        the gate degrades to Pause / Cancel only.
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

        # Build offered_modes dynamically. Pause + Cancel are always present;
        # api_extended renders when the extension config allows it and there
        # is enough daily / monthly headroom.
        offered_modes: list[str] = []
        if await self._should_offer_extension(estimate_usd, user_id):
            offered_modes.append("api_extended")
        # Slice 20 — per-task-type chat mode. Only offer when:
        #   1. The master kill-switch is on (already checked above).
        #   2. Modes.chat is enabled (YAML, override-able via dashboard).
        #   3. The task type declares ``manual_escalation: {mode: chat}``.
        #   4. The caller passed an ``original_prompt`` for us to render.
        # Same shape will be reused for ``claude_code`` in slice 21.
        chat_eligible = await self._chat_mode_eligible(
            task_type=task_type, original_prompt=original_prompt
        )
        if chat_eligible:
            offered_modes.append("chat")
        offered_modes.extend(["pause", "cancel"])

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

        # Render the chat-mode prompt + summary BEFORE the delivery
        # callback runs so the Discord notification can attach the .md
        # alongside the summary text. Best-effort: failures here are
        # logged inside the builder but do not abort the escalation —
        # the row still exists, the buttons still render, and the user
        # can fall back to Pause / Cancel.
        if (
            chat_eligible
            and self._chat_prompt_builder is not None
            and original_prompt is not None
        ):
            try:
                await self._chat_prompt_builder.build_and_persist(
                    conn=self._repo._conn,
                    row=row,
                    original_prompt=original_prompt,
                )
                # Re-read the row so downstream consumers (delivery
                # callback) see the freshly-persisted summary + prompt
                # path without an extra round trip.
                refreshed = await self._repo.get(row.id)
                if refreshed is not None:
                    row = refreshed
            except Exception:
                logger.exception(
                    "escalation_chat_prompt_build_failed",
                    correlation_id=correlation_id,
                    escalation_request_id=row.id,
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
            extension_amount: float | None = None
            if resolved.resolution == "api_extended":
                extension_amount = resolved.estimate_usd
            return GateOutcome(
                fired=True,
                mode=_coerce_mode(resolved.resolution),
                resolved_by=_coerce_resolved_by(resolved.resolved_by),
                escalation_request_id=row.id,
                correlation_id=correlation_id,
                extension_amount_usd=extension_amount,
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

    async def grant_budget_extension(
        self,
        *,
        correlation_id: str,
        granted_by: str,
    ) -> DailyBudgetExtensionRow | None:
        """Grant a budget extension for the given escalation.

        Called by the Discord button callback BEFORE ``record_user_resolution``
        so the extension row exists before the resolution event fires. The
        operation is idempotent: a Discord retry will find the existing row
        and return it unchanged without double-granting.

        Returns:
            The new or existing ``DailyBudgetExtensionRow``, or ``None``
            if the escalation row cannot be found or DB insertion fails.
        """
        row = await self._repo.get_by_correlation(correlation_id)
        if row is None:
            logger.warning(
                "grant_budget_extension_no_row",
                correlation_id=correlation_id,
            )
            return None

        # Guard: enforce monthly ceiling before granting.
        today = date.today()
        if not await self._monthly_headroom_ok(row.user_id, today, row.estimate_usd):
            logger.warning(
                "grant_budget_extension_monthly_ceiling",
                correlation_id=correlation_id,
                user_id=row.user_id,
            )
            return None

        extension = await self._extension_repo.grant(
            user_id=row.user_id,
            for_date=today,
            amount_usd=row.estimate_usd,
            granted_by=granted_by,
            escalation_request_id=row.id,
            now=datetime.now(tz=UTC),
        )
        if extension is None:
            return None

        # Audit log (idempotent: duplicate audit rows are acceptable on retry).
        try:
            await write_escalation_event(
                self._repo._conn,
                event=EVENT_EXTENSION_GRANTED,
                escalation_request_id=row.id,
                correlation_id=correlation_id,
                user_id=row.user_id,
                task_id=row.task_id,
                payload={
                    "extension_id": extension.id,
                    "amount_usd": extension.amount_usd,
                    "granted_by": granted_by,
                },
            )
        except Exception:
            logger.exception(
                "grant_budget_extension_audit_failed",
                correlation_id=correlation_id,
            )
        return extension

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _is_enabled(self) -> bool:
        """Resolve the master kill switch (dashboard → YAML)."""
        return await self._resolver.get(
            "manual_escalation.enabled", self._config.enabled
        )

    async def _chat_mode_eligible(
        self,
        *,
        task_type: str,
        original_prompt: str | None,
    ) -> bool:
        """Return True iff the chat-mode button should render this call.

        The four-pronged check (spec §5.2 / §6.1 / §6.2):
          1. ``original_prompt`` was supplied — chat mode is meaningless
             without something to render into the prompt body.
          2. The chat prompt builder is wired (slice 20 dependency
             injection — tests + minimal boots can omit it).
          3. The task type's :class:`TaskTypeEntry` declares
             ``manual_escalation.mode='chat'``.
          4. The runtime ``modes.chat.enabled`` flag is on (resolved
             through the dashboard override layer).
        """
        if original_prompt is None or self._chat_prompt_builder is None:
            return False
        if self._task_types_config is None:
            return False
        entry = self._task_types_config.task_types.get(task_type)
        if entry is None or entry.manual_escalation is None:
            return False
        if entry.manual_escalation.mode != "chat":
            return False
        return await self._resolver.get(
            "manual_escalation.modes.chat.enabled",
            self._config.modes.chat.enabled,
        )

    async def _should_offer_extension(
        self, estimate_usd: float, user_id: str
    ) -> bool:
        """Return True if the api_extended button should be rendered.

        Checks (in order):
        1. Budget extension enabled (dashboard → YAML).
        2. Estimate fits within remaining daily headroom.
        3. Monthly ceiling not reached.
        """
        ext_cfg = self._config.budget_extension
        enabled: bool = await self._resolver.get(
            "budget_extension.enabled", ext_cfg.enabled
        )
        if not enabled:
            return False

        today = date.today()
        existing_total = await self._extension_repo.get_daily_total(user_id, today)
        max_daily = ext_cfg.max_daily_extension_usd
        headroom = max_daily - existing_total
        if headroom < estimate_usd:
            return False

        return await self._monthly_headroom_ok(user_id, today, estimate_usd)

    async def _monthly_headroom_ok(
        self, user_id: str, today: date, estimate_usd: float
    ) -> bool:
        """Return True if the monthly ceiling would not be breached."""
        ext_cfg = self._config.budget_extension
        monthly_total = await self._extension_repo.get_monthly_total(
            user_id, today.year, today.month
        )
        return (monthly_total + estimate_usd) <= ext_cfg.hard_monthly_ceiling_usd

    async def _daily_remaining(self, user_id: str) -> float:
        """Compute today's remaining budget envelope (including extensions).

        Mirrors :class:`donna.cost.budget.BudgetGuard` exclusions so the
        gate's accounting matches the rest of the cost subsystem. Extensions
        raise the effective cap so already-approved spend isn't double-counted.
        """
        summary = await self._tracker.get_daily_cost(
            exclude_task_types=["external_llm_call", "escalation_lifecycle"]
        )
        extension_total = await self._extension_repo.get_daily_total(
            user_id, date.today()
        )
        effective_cap = self._daily_pause_threshold_usd + extension_total
        remaining = effective_cap - summary.total_usd
        return max(0.0, remaining)


def _coerce_mode(raw: str) -> EscalationMode:
    if raw not in {"pause", "cancel", "api_extended", "chat", "claude_code"}:
        logger.warning("escalation_unknown_resolution", resolution=raw)
        return "pause"
    return raw  # type: ignore[return-value]


def _coerce_resolved_by(raw: str | None) -> ResolvedBy:
    if raw == "timeout":
        return "timeout"
    return "user"
