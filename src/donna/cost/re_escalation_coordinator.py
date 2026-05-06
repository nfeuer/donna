"""Token-cap recovery for the over-budget gate (slice 25).

Realises docs/superpowers/specs/manual-escalation.md §10.6 row 1
("re-estimate + re-escalation") and §12 Q5
(``max_re_escalation_depth``).

The flow:

    ModelRouter.complete()
        --> escalation_gate.fire_and_wait()  → api_extended granted
        --> provider.complete() with max_tokens cap
        --> TokenLimitReachedError raised (input cost > extension OR
            metadata.token_limited == True)
        --> ReEscalationCoordinator.recover()
              * walks parent chain via repo.find_chain_depth
              * computes a re-estimated spend (previous × multiplier,
                clamped to monthly headroom)
              * re-fires the gate with parent_escalation_id set
              * returns the new GateOutcome (api_extended → recurse;
                anything else → caller's existing handler)

Bounded by two layers:

  1. ``max_in_flight_attempts`` (this module, default 3) — defensive
     guard against a misconfigured persisted depth allowing an
     unbounded chain of recursive ``complete()`` calls. Bounds the
     in-process loop only.
  2. ``triggers.max_re_escalation_depth`` (the gate, default 5) — the
     user-facing truth surfaced on the dashboard timeline. Enforced
     inside :meth:`EscalationGate.fire_and_wait`; the coordinator
     also pre-checks it so a chain-capped recovery fast-fails before
     re-rendering Discord buttons the user will never see.

Both layers exist because (a) the persisted cap can be runtime-mutated
through the slice-23 dashboard while a recovery loop is in progress,
and (b) a config mistake setting the persisted cap to an absurd value
should not be able to drive the in-process loop into pathological
recursion.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import TYPE_CHECKING

import structlog

from donna.cost.escalation_audit import (
    EVENT_RE_ESCALATION_TOKEN_LIMITED,
    write_escalation_event,
)

if TYPE_CHECKING:
    from donna.config import ManualEscalationConfig
    from donna.cost.budget_extension import BudgetExtensionRepository
    from donna.cost.escalation_gate import EscalationGate, GateOutcome
    from donna.cost.escalation_repository import EscalationRepository
    from donna.models.router import TokenLimitReachedError

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class RecoveryDecision:
    """Result of one re-escalation pass.

    ``outcome`` carries the gate's resolution. The router inspects
    ``outcome.mode`` to decide whether to re-call ``complete()`` (on
    ``api_extended``) or surface the resolution back to the caller
    (everything else).

    ``new_estimate_usd`` is the estimate the coordinator asked the gate
    to fire on; useful for audit / log clarity.

    ``chain_capped`` is True when the coordinator (or the gate)
    refused to fire because the persisted depth cap was reached. The
    router re-raises the original :class:`TokenLimitReachedError` so
    the caller's existing failure path runs.
    """

    outcome: GateOutcome
    new_estimate_usd: float
    chain_capped: bool


class ReEscalationCoordinator:
    """Catches token-cap errors and re-fires the gate.

    Constructor args:
        gate: The wired :class:`EscalationGate`. Re-fires go through
            ``gate.fire_and_wait`` with ``parent_escalation_id`` set so
            chain depth is enforced inside the gate.
        repo: :class:`EscalationRepository`. Used for chain-depth
            inspection and the audit log.
        extension_repo: :class:`BudgetExtensionRepository`. Used to
            clamp the re-estimate against monthly headroom.
        manual_escalation_config: Source of truth for
            ``triggers.re_escalation_estimate_multiplier``,
            ``triggers.max_re_escalation_depth`` (defaults), and
            ``budget_extension.hard_monthly_ceiling_usd``.
        max_in_flight_attempts: Defensive in-process bound. Default 3.
    """

    def __init__(
        self,
        *,
        gate: EscalationGate,
        repo: EscalationRepository,
        extension_repo: BudgetExtensionRepository,
        manual_escalation_config: ManualEscalationConfig,
        max_in_flight_attempts: int = 3,
    ) -> None:
        self._gate = gate
        self._repo = repo
        self._extension_repo = extension_repo
        self._config = manual_escalation_config
        self._max_in_flight_attempts = max(1, max_in_flight_attempts)

    async def recover(
        self,
        *,
        token_error: TokenLimitReachedError,
        user_id: str,
        task_id: str | None,
        task_type: str,
        priority: int,
        originating_entity: tuple[str, str] | None,
        target_paths: dict[str, str] | None,
        base_sha: str | None,
        original_prompt: str,
        previous_estimate_usd: float,
        previous_extension_usd: float | None,
        attempts_remaining: int | None = None,
    ) -> RecoveryDecision:
        """Re-fire the gate with a re-estimated spend.

        Args:
            token_error: The error that triggered recovery — its
                ``escalation_request_id`` becomes the new row's
                ``parent_escalation_id``.
            user_id, task_id, task_type, priority, originating_entity,
            target_paths, base_sha, original_prompt:
                Threaded directly to :meth:`EscalationGate.fire_and_wait`.
                Identical to the original call so the gate sees the
                same task in the same context.
            previous_estimate_usd: The estimate the parent fired on.
            previous_extension_usd: The amount the parent's extension
                granted (or None if the parent fired without
                ``api_extended``). Used as a floor for the new
                estimate when known.
            attempts_remaining: Coordinator's local in-flight cap;
                defaults to ``max_in_flight_attempts`` on first call,
                decremented by the router on each recursion. When 0
                the coordinator refuses to re-fire and writes a
                ``re_escalation_token_limited`` audit row.

        Returns:
            :class:`RecoveryDecision`. The router routes on the
            decision's outcome.
        """
        if attempts_remaining is None:
            attempts_remaining = self._max_in_flight_attempts

        parent_id = token_error.escalation_request_id
        parent_correlation_id = token_error.correlation_id

        if attempts_remaining <= 0:
            await self._write_token_limited_audit(
                parent_id=parent_id,
                parent_correlation_id=parent_correlation_id,
                user_id=user_id,
                task_id=task_id,
                last_outcome_mode="in_flight_attempts_exhausted",
            )
            return RecoveryDecision(
                outcome=self._synthetic_cancel(parent_id, parent_correlation_id),
                new_estimate_usd=previous_estimate_usd,
                chain_capped=True,
            )

        # Pre-check the persisted depth cap. The gate enforces it too —
        # we check here so the chain-cap fast-fail path skips
        # rendering Discord buttons the user will never see, and so
        # the audit chain explicitly carries `re_escalation_token_limited`
        # rather than letting the gate's `re_escalation_chain_capped`
        # be the only marker.
        cap = int(self._config.triggers.max_re_escalation_depth)
        parent_depth = await self._repo.find_chain_depth(parent_id)
        if (parent_depth + 1) > cap:
            await self._write_token_limited_audit(
                parent_id=parent_id,
                parent_correlation_id=parent_correlation_id,
                user_id=user_id,
                task_id=task_id,
                last_outcome_mode="chain_cap_reached",
            )
            return RecoveryDecision(
                outcome=self._synthetic_cancel(parent_id, parent_correlation_id),
                new_estimate_usd=previous_estimate_usd,
                chain_capped=True,
            )

        new_estimate_usd = await self._compute_new_estimate(
            user_id=user_id,
            previous_estimate_usd=previous_estimate_usd,
            previous_extension_usd=previous_extension_usd,
        )

        logger.info(
            "re_escalation_coordinator_firing",
            parent_id=parent_id,
            parent_correlation_id=parent_correlation_id,
            previous_estimate_usd=previous_estimate_usd,
            new_estimate_usd=new_estimate_usd,
            parent_depth=parent_depth,
            cap=cap,
            attempts_remaining=attempts_remaining,
        )

        outcome = await self._gate.fire_and_wait(
            user_id=user_id,
            task_id=task_id,
            task_type=task_type,
            estimate_usd=new_estimate_usd,
            priority=priority,
            originating_entity=originating_entity,
            target_paths=target_paths,
            base_sha=base_sha,
            original_prompt=original_prompt,
            parent_escalation_id=parent_id,
        )

        # The gate's chain-cap path returns a synthetic cancel keyed
        # off the parent's id. Detect that explicitly by checking the
        # row id against the parent.
        chain_capped = (
            outcome.fired
            and outcome.mode == "cancel"
            and outcome.escalation_request_id == parent_id
        )
        if chain_capped:
            await self._write_token_limited_audit(
                parent_id=parent_id,
                parent_correlation_id=parent_correlation_id,
                user_id=user_id,
                task_id=task_id,
                last_outcome_mode="gate_chain_capped",
            )
        elif outcome.fired and outcome.mode != "api_extended":
            # The user resolved the recovery to a non-recoverable mode
            # (chat, claude_code, pause, manual cancel). Audit the
            # chain termination so the dashboard timeline carries an
            # explicit "we stopped re-firing" marker.
            await self._write_token_limited_audit(
                parent_id=parent_id,
                parent_correlation_id=parent_correlation_id,
                user_id=user_id,
                task_id=task_id,
                last_outcome_mode=outcome.mode or "unknown",
            )

        return RecoveryDecision(
            outcome=outcome,
            new_estimate_usd=new_estimate_usd,
            chain_capped=chain_capped,
        )

    async def _compute_new_estimate(
        self,
        *,
        user_id: str,
        previous_estimate_usd: float,
        previous_extension_usd: float | None,
    ) -> float:
        """Apply the multiplier and clamp to monthly headroom.

        ``previous_extension_usd`` (if known) acts as a floor so the
        re-estimate cannot accidentally request *less* than the parent
        already had — the parent ran out of cap, after all. The
        multiplier sits in
        ``triggers.re_escalation_estimate_multiplier`` (default 2.0).
        Clamped to remaining month-to-date headroom under
        ``budget_extension.hard_monthly_ceiling_usd`` so the gate's
        ``_should_offer_extension`` path can still grant.
        """
        multiplier = float(
            self._config.triggers.re_escalation_estimate_multiplier
        )
        if multiplier <= 0:
            multiplier = 2.0
        floor = max(
            previous_estimate_usd,
            float(previous_extension_usd or 0.0),
        )
        candidate = floor * multiplier

        today = date.today()
        monthly_total = await self._extension_repo.get_monthly_total(
            user_id, today.year, today.month
        )
        ceiling = float(self._config.budget_extension.hard_monthly_ceiling_usd)
        monthly_headroom = max(0.0, ceiling - monthly_total)

        clamped = min(candidate, monthly_headroom) if monthly_headroom > 0 else 0.0
        # Guard against pathological zeros — at least surface the
        # previous estimate so the gate has something to evaluate.
        if clamped < previous_estimate_usd:
            clamped = previous_estimate_usd
        return clamped

    def _synthetic_cancel(
        self, parent_id: int, parent_correlation_id: str
    ) -> GateOutcome:
        """Build a chain-capped cancel outcome without touching the gate."""
        from donna.cost.escalation_gate import GateOutcome

        return GateOutcome(
            fired=True,
            mode="cancel",
            resolved_by="system",
            escalation_request_id=parent_id,
            correlation_id=parent_correlation_id,
        )

    async def _write_token_limited_audit(
        self,
        *,
        parent_id: int,
        parent_correlation_id: str,
        user_id: str,
        task_id: str | None,
        last_outcome_mode: str,
    ) -> None:
        depth = await self._repo.find_chain_depth(parent_id)
        try:
            await write_escalation_event(
                self._repo._conn,
                event=EVENT_RE_ESCALATION_TOKEN_LIMITED,
                escalation_request_id=parent_id,
                correlation_id=parent_correlation_id,
                user_id=user_id,
                task_id=task_id,
                payload={
                    "root_correlation_id": parent_correlation_id,
                    "depth": depth,
                    "last_outcome_mode": last_outcome_mode,
                },
            )
        except Exception:
            logger.exception(
                "re_escalation_token_limited_audit_failed",
                parent_id=parent_id,
            )
