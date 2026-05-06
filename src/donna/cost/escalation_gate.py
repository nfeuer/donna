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
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import structlog
import uuid6

from donna.config import ManualEscalationConfig, TaskTypesConfig
from donna.cost.budget_extension import BudgetExtensionRepository, DailyBudgetExtensionRow
from donna.cost.claude_code_spec import ClaudeCodeSpecBuilder, RenderedSpec
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import (
    EVENT_EXTENSION_GRANTED,
    EVENT_OFFERED,
    EVENT_RESOLVED,
    write_escalation_event,
)
from donna.cost.escalation_chat_prompt import ChatPromptBuilder
from donna.cost.escalation_repository import (
    EscalationRepository,
    EscalationRequestRow,
)
from donna.cost.tracker import CostTracker

if TYPE_CHECKING:
    pass

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
        spec_builder: ClaudeCodeSpecBuilder | None = None,
        host_repo: Any = None,
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
        # Slice 21 wiring — claude_code spec rendering + host repo for
        # base_sha capture. Optional; absence disables the claude_code
        # button without crashing boot.
        self._spec_builder = spec_builder
        self._host_repo = host_repo

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
        originating_entity: tuple[str, str] | None = None,
        target_paths: dict[str, str] | None = None,
        base_sha: str | None = None,
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
          * ``fired=True, mode='claude_code'`` — slice 21 manual handoff.
            Caller treats the task as parked; the ClaudeCodePoller
            validates the user's branch and updates lifecycle state on
            success.

        ``original_prompt`` is the fully-rendered prompt the caller would
        otherwise have sent to the API. Slice 20 uses it (with the chat
        prompt builder) to produce the prompt body the user pastes into
        Claude. Without it, chat mode cannot be offered for this call —
        the gate degrades to Pause / Cancel only.

        ``originating_entity`` (slice 21) is the FK pair the
        claude_code poller uses to render ``{name}``-substituted
        target_paths globs. ``task_id`` is NULL for skill_auto_draft
        and skill_evolution call sites, so this kwarg is the only way
        the validator can identify the target.

        ``target_paths`` and ``base_sha`` snapshot the manual_escalation
        scope at gate-fire time. Both are persisted on the row so a
        config edit mid-flight cannot widen scope retroactively
        (spec §10.7 row 2) and the worktree command stays pinned to a
        specific main SHA (spec §5.3 / drift mitigation).
        """
        if not await self._is_enabled():
            return GateOutcome(
                fired=False,
                mode=None,
                resolved_by=None,
                escalation_request_id=None,
                correlation_id=None,
            )

        # De-dup: if a previous claude_code escalation for this same
        # entity is still in-flight, refuse to open a parallel race
        # (spec §10.7 / brainstorm decision §21). We only RE-DELIVER
        # the Discord notification when the prior row is still in
        # ``open`` state; re-delivering for ``resolved`` (user clicked
        # but hasn't built yet) / ``submitted`` (poller is validating)
        # / ``failed`` (user is iterating) would just spam them about
        # work they already know is in flight.
        if originating_entity is not None:
            existing = await self._repo.find_open_for_originating_entity(
                entity_type=originating_entity[0],
                entity_id=originating_entity[1],
            )
            if existing is not None and (
                existing.mode == "claude_code"
                or "claude_code" in existing.offered_modes
            ):
                logger.info(
                    "escalation_dedup_existing_claude_code",
                    correlation_id=existing.correlation_id,
                    existing_status=existing.status,
                    originating_entity=originating_entity,
                )
                if existing.status == "open":
                    # User still hasn't seen / clicked the existing
                    # ping — a fresh delivery may help.
                    try:
                        await self._deliver(existing)
                    except Exception:
                        logger.exception(
                            "escalation_redeliver_failed",
                            correlation_id=existing.correlation_id,
                        )
                # Don't await resolution — return as if not fired so
                # the caller falls back to ``BudgetPausedError`` /
                # paused state. Spawning a parallel awaiter would race.
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
        # is enough daily / monthly headroom; claude_code (slice 21) renders
        # when the per-task-type config + host_repo + spec_builder line up.
        offered_modes: list[str] = []
        if await self._should_offer_extension(estimate_usd, user_id):
            offered_modes.append("api_extended")
        # Slice 20 — per-task-type chat mode. Only offer when:
        #   1. The master kill-switch is on (already checked above).
        #   2. Modes.chat is enabled (YAML, override-able via dashboard).
        #   3. The task type declares ``manual_escalation: {mode: chat}``.
        #   4. The caller passed an ``original_prompt`` for us to render.
        chat_eligible = await self._chat_mode_eligible(
            task_type=task_type, original_prompt=original_prompt
        )
        if chat_eligible:
            offered_modes.append("chat")
        # Slice 21 — claude_code mode. Only offer when:
        #   1. The master kill-switch is on (already checked above).
        #   2. Modes.claude_code is enabled (YAML, dashboard).
        #   3. The task type declares ``manual_escalation: {mode: claude_code}``.
        #   4. The host repo + spec builder are configured (cli_wiring).
        if await self._should_offer_claude_code(task_type):
            offered_modes.append("claude_code")
            # If the gate caller didn't pre-render target_paths, do it
            # now from the per-task-type config so the row carries the
            # exact scope at fire time (spec §10.7 row 2 — config can
            # change mid-flight).
            if target_paths is None and self._task_types_config is not None:
                target_paths = self._render_target_paths(task_type)
            # Capture base_sha if we can; the gate's caller may also
            # pre-supply one. Failing-soft: missing base_sha just means
            # the spec will reference the symbolic ref instead.
            if base_sha is None and self._host_repo is not None:
                try:
                    base_sha = await self._host_repo.rev_parse(
                        f"refs/heads/{self._config.modes.claude_code.base_ref}"
                    )
                except Exception:
                    base_sha = None
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
            originating_entity=originating_entity,
            target_paths=target_paths,
            base_sha=base_sha,
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
    # Slice 22 — tool build escalation entry (no cost gate)
    # ------------------------------------------------------------------

    async def open_tool_build_escalation(
        self,
        *,
        tool_request_id: int,
        tool_name: str,
        user_id: str,
        priority: int = 3,
        actor_id: str | None = None,
        proposed_signature: dict[str, Any] | None = None,
    ) -> tuple[EscalationRequestRow, RenderedSpec | None]:
        """Open a ``tool_request_fulfillment`` escalation directly.

        Bypasses :meth:`fire_and_wait` because tool builds have no API
        spend to gate — the user already chose to fulfill the request
        by clicking ``[File request]`` on a
        :class:`donna.integrations.discord_views.ToolGapPingView`.

        Creates the ``escalation_request`` row with
        ``originating_entity=('tool_request', <id>)`` and
        ``offered_modes=['claude_code']``, then immediately renders the
        spec via :meth:`record_manual_handoff` (status flips to
        ``resolved``). Returns the row plus the rendered spec.

        Args:
            tool_request_id: FK back to the ``tool_request`` row.
            tool_name: Used as the ``{name}`` substitution into
                ``target_paths`` globs.
            user_id: Owner.
            priority: Inherited from the tool_request.
            actor_id: Discord ID of the clicker (logged on resolution).
            proposed_signature: Optional sketch passed through the
                spec renderer's Jinja context.
        """
        if not await self._is_enabled():
            logger.info(
                "tool_build_escalation_master_disabled",
                tool_request_id=tool_request_id,
            )
            return await self._abort_disabled(
                user_id=user_id,
                tool_request_id=tool_request_id,
                tool_name=tool_name,
            )
        if not await self._should_offer_claude_code("tool_request_fulfillment"):
            logger.warning(
                "tool_build_escalation_claude_code_unavailable",
                tool_request_id=tool_request_id,
            )
            return await self._abort_disabled(
                user_id=user_id,
                tool_request_id=tool_request_id,
                tool_name=tool_name,
            )

        # De-dup: refuse if an open/in-flight escalation already exists
        # for this tool_request, to mirror slice 21's skill de-dup
        # (manual-escalation.md §5.3 "De-dup").
        existing = await self._repo.find_open_for_originating_entity(
            "tool_request", str(tool_request_id)
        )
        if existing is not None:
            logger.info(
                "tool_build_escalation_dedup_hit",
                tool_request_id=tool_request_id,
                existing_id=existing.id,
                status=existing.status,
            )
            return existing, None

        # Snapshot scope + base SHA up-front (mirrors fire_and_wait's
        # claude_code branch).
        target_paths = self._render_target_paths("tool_request_fulfillment")
        base_sha: str | None = None
        if self._host_repo is not None:
            try:
                base_sha = await self._host_repo.rev_parse(
                    f"refs/heads/{self._config.modes.claude_code.base_ref}"
                )
            except Exception:
                base_sha = None

        correlation_id = str(uuid6.uuid7())
        row = await self._repo.create(
            user_id=user_id,
            correlation_id=correlation_id,
            task_id=None,
            task_type="tool_request_fulfillment",
            estimate_usd=0.0,
            daily_remaining_usd=await self._daily_remaining(user_id),
            offered_modes=["claude_code"],
            priority=priority,
            originating_entity=("tool_request", str(tool_request_id)),
            target_paths=target_paths,
            base_sha=base_sha,
        )
        await write_escalation_event(
            self._repo._conn,
            event=EVENT_OFFERED,
            escalation_request_id=row.id,
            correlation_id=correlation_id,
            user_id=user_id,
            task_id=None,
            payload={
                "task_type": "tool_request_fulfillment",
                "offered_modes": ["claude_code"],
                "tool_request_id": tool_request_id,
                "tool_name": tool_name,
                "estimate_usd": 0.0,
            },
        )

        rendered = await self.record_manual_handoff(
            correlation_id=correlation_id,
            mode="claude_code",
            capability_name=tool_name,
            actor_id=actor_id,
            task_summary=(
                f"Build new tool '{tool_name}' to unblock pending capabilities."
            ),
            acceptance_criteria=_tool_build_acceptance_criteria(
                tool_name, proposed_signature
            ),
            extra_context={
                "proposed_signature": proposed_signature,
                "requires_rebuild_default": False,
                "default_timeout_seconds": 5,
            },
        )
        # Re-fetch so the caller sees the post-handoff status.
        refreshed = await self._repo.get(row.id)
        return refreshed or row, rendered

    async def _abort_disabled(
        self,
        *,
        user_id: str,
        tool_request_id: int,
        tool_name: str,
    ) -> tuple[EscalationRequestRow, RenderedSpec | None]:
        """Internal: return a synthetic 'cancelled' row when disabled.

        Used when the master kill-switch is off or claude_code mode is
        unavailable. We still create the row so the audit trail captures
        the click; the caller should surface a Discord message saying
        "tool builds are disabled — flip the toggle and try again".
        """
        correlation_id = str(uuid6.uuid7())
        row = await self._repo.create(
            user_id=user_id,
            correlation_id=correlation_id,
            task_id=None,
            task_type="tool_request_fulfillment",
            estimate_usd=0.0,
            daily_remaining_usd=0.0,
            offered_modes=[],
            priority=1,
            originating_entity=("tool_request", str(tool_request_id)),
            target_paths=None,
            base_sha=None,
        )
        await self._repo.resolve(
            row.id, resolution="cancel", resolved_by="system"
        )
        return row, None

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

    async def _should_offer_claude_code(self, task_type: str) -> bool:
        """Slice 21: gate the claude_code button on full preconditions.

        All four must hold:
        1. ``modes.claude_code.enabled`` (dashboard → YAML).
        2. The host repo is mounted (``self._host_repo`` is not None).
        3. The spec builder was wired (cli_wiring passes it when paths
           resolve at boot).
        4. The task type declares ``manual_escalation.mode == "claude_code"``.
        """
        if self._spec_builder is None or self._host_repo is None:
            return False
        if self._task_types_config is None:
            return False
        cc_enabled: bool = await self._resolver.get(
            "modes.claude_code.enabled", self._config.modes.claude_code.enabled
        )
        if not cc_enabled:
            return False
        entry = self._task_types_config.task_types.get(task_type)
        if entry is None or entry.manual_escalation is None:
            return False
        return bool(entry.manual_escalation.mode == "claude_code")

    async def _resolve_capability_name(
        self, row: EscalationRequestRow
    ) -> str | None:
        """Look up capability name from originating_entity_*.

        Mirrors the same logic in
        :class:`donna.cost.manual_validation_router.ManualValidationRouter._resolve_capability_name`
        so the gate-side spec render and the validator-side scope check
        agree on the substituted name.
        """
        ent_type = row.originating_entity_type
        ent_id = row.originating_entity_id
        if ent_type is None or ent_id is None:
            return None
        if ent_type == "skill_candidate_report":
            cursor = await self._repo._conn.execute(
                "SELECT capability_name FROM skill_candidate_report WHERE id = ?",
                (ent_id,),
            )
            r = await cursor.fetchone()
            return str(r[0]) if r and r[0] else None
        if ent_type == "skill":
            cursor = await self._repo._conn.execute(
                "SELECT capability_name FROM skill WHERE id = ?",
                (ent_id,),
            )
            r = await cursor.fetchone()
            return str(r[0]) if r and r[0] else None
        if ent_type == "tool_request":
            # Slice 22: the "name" substituted into target_paths globs
            # for tool builds is the tool name itself (used as
            # ``src/donna/skills/tools/{name}.py``).
            cursor = await self._repo._conn.execute(
                "SELECT tool_name FROM tool_request WHERE id = ?",
                (ent_id,),
            )
            r = await cursor.fetchone()
            return str(r[0]) if r and r[0] else None
        return None

    def _render_target_paths(self, task_type: str) -> dict[str, str] | None:
        """Snapshot ``target_paths`` from per-task-type config (un-substituted).

        ``{name}`` substitution happens later in the spec builder, when
        the originating-entity name is resolved. The row stores the
        substituted form once :meth:`record_manual_handoff` runs; until
        then the row carries the un-substituted globs as a snapshot.
        """
        if self._task_types_config is None:
            return None
        entry = self._task_types_config.task_types.get(task_type)
        if entry is None or entry.manual_escalation is None:
            return None
        return dict(entry.manual_escalation.target_paths or {})

    async def record_manual_handoff(
        self,
        *,
        correlation_id: str,
        mode: str,
        capability_name: str | None = None,
        actor_id: str | None = None,
        task_summary: str | None = None,
        acceptance_criteria: list[str] | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> RenderedSpec | None:
        """Resolve an open escalation as a manual handoff (slice 21).

        Mirrors the slice 18 ``grant_budget_extension`` precedent: an
        idempotent helper that mutates the row BEFORE the resolution
        event fires so the dashboard already has data when the user
        follows the Discord link.

        For ``mode='claude_code'``: renders the spec template, writes
        it to disk, mirrors into ``escalation_request.prompt_body``,
        marks ``status='resolved'`` with ``resolution='claude_code'``.

        Returns the :class:`RenderedSpec` (so the caller can attach
        the file in Discord) or ``None`` on failure.
        """
        if mode != "claude_code":
            logger.warning(
                "record_manual_handoff_unsupported_mode",
                mode=mode,
            )
            return None
        if self._spec_builder is None:
            logger.warning(
                "record_manual_handoff_no_spec_builder",
                correlation_id=correlation_id,
            )
            return None

        row = await self._repo.get_by_correlation(correlation_id)
        if row is None:
            return None
        if self._task_types_config is None:
            return None
        entry = self._task_types_config.task_types.get(row.task_type)
        if entry is None or entry.manual_escalation is None:
            return None

        if capability_name is None:
            capability_name = await self._resolve_capability_name(row)
            if capability_name is None:
                logger.warning(
                    "record_manual_handoff_no_capability",
                    correlation_id=correlation_id,
                    originating_entity_type=row.originating_entity_type,
                    originating_entity_id=row.originating_entity_id,
                )
                return None

        try:
            rendered = self._spec_builder.render(
                correlation_id=correlation_id,
                task_type=row.task_type,
                capability_name=capability_name,
                manual=entry.manual_escalation,
                base_sha=row.base_sha or "HEAD",
                task_summary=task_summary or _default_summary(row),
                acceptance_criteria=acceptance_criteria or _default_acceptance(),
                extra_context=extra_context,
            )
        except Exception:
            logger.exception(
                "record_manual_handoff_render_failed",
                correlation_id=correlation_id,
            )
            return None

        await self._repo.set_manual_handoff(
            row.id,
            mode="claude_code",
            prompt_path=str(rendered.path),
            prompt_body=rendered.body,
        )
        ok = await self._repo.resolve(
            row.id, resolution="claude_code", resolved_by=actor_id or "user"
        )
        if not ok:
            # Already resolved by another click / sweep — return the
            # rendered spec anyway so the caller can still surface it.
            logger.info(
                "record_manual_handoff_already_resolved",
                correlation_id=correlation_id,
            )

        await write_escalation_event(
            self._repo._conn,
            event=EVENT_RESOLVED,
            escalation_request_id=row.id,
            correlation_id=correlation_id,
            user_id=row.user_id,
            task_id=row.task_id,
            payload={
                "mode": "claude_code",
                "resolved_by": "user",
                "spec_path": str(rendered.path),
                "branch_name": rendered.branch_name,
            },
        )
        EscalationGate.signal_resolution(correlation_id)
        return rendered

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
        return bool(await self._resolver.get(
            "manual_escalation.modes.chat.enabled",
            self._config.modes.chat.enabled,
        ))

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


def _default_summary(row: EscalationRequestRow) -> str:
    """Fallback task summary when the gate's caller doesn't supply one.

    Mirrors the deterministic summary slice 17 / 20 use when the local
    Ollama summarizer is unavailable.
    """
    return (
        f"{row.task_type} request — estimate ${row.estimate_usd:.2f}. "
        "Build per the spec below; commit on the branch named in the "
        "worktree command."
    )


def _default_acceptance() -> list[str]:
    """Fallback acceptance criteria — assumes a skill build."""
    return [
        "skill.yaml + steps/* + schemas/* committed under skills/{name}/",
        "fixtures committed under fixtures/{name}/<case>.json",
        "ValidationExecutor pass rate >= configured threshold (default 0.8)",
        "no files touched outside the declared target_paths",
        "no forbidden patterns in any new commits",
    ]


def _tool_build_acceptance_criteria(
    tool_name: str,
    proposed_signature: dict[str, Any] | None,
) -> list[str]:
    """Slice 22 — tool build acceptance criteria (template hints).

    The spec template (``prompts/escalation/tool_build.md``) renders
    these as a checklist for the user. They mirror the §10.5 lint rules
    so the user knows what validation will assert.
    """
    sig_hint = ""
    if proposed_signature and proposed_signature.get("summary"):
        sig_hint = f" (summary: {proposed_signature['summary']})"
    return [
        f"src/donna/skills/tools/{tool_name}.py present and importable{sig_hint}",
        "module-level `requires_rebuild = <bool>` and `default_timeout_seconds = <int>` declared",
        f"tests/skills/tools/test_{tool_name}.py present, calls "
        f"is_inert_at_import('donna.skills.tools.{tool_name}')",
        "tool added to at least one allowlist (config/agents.yaml | "
        "skills.yaml | task_types.yaml) OR module declares unallowlisted = True",
        "no `import anthropic` outside src/donna/llm/",
        "no module-level network/disk I/O",
        "no hardcoded credentials — use vault.read('<name>')",
        "no files touched outside the declared target_paths",
    ]
