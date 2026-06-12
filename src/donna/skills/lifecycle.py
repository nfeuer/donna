"""SkillLifecycleManager — sole mutator of skill.state.

Enforces all state-machine transitions defined in docs/skills-system.md §6.2.
Every successful state change writes a ``skill_state_transition`` audit row.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import aiosqlite
import structlog
import uuid6

from donna.config import SkillSystemConfig
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()


class _AfterStateChangeHook:
    """Fan-out hook fired after every successful skill state transition.

    Subscribers receive ``(capability_name, new_state)`` where ``new_state`` is
    the string value of the destination :class:`SkillState`. Subscriber
    exceptions are caught and logged so a bad subscriber never breaks the
    underlying state transition.
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[[str, str], Awaitable[None]]] = []

    def register(self, fn: Callable[[str, str], Awaitable[None]]) -> None:
        self._subscribers.append(fn)

    async def fire(self, capability_name: str, new_state: str) -> None:
        for fn in self._subscribers:
            try:
                await fn(capability_name, new_state)
            except Exception as exc:
                logger.exception(
                    "after_state_change_subscriber_failed",
                    error=str(exc),
                    capability=capability_name,
                    new_state=new_state,
                )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IllegalTransitionError(Exception):
    """Raised when a requested state transition is not permitted."""


class HumanGateRequiredError(Exception):
    """Raised when ``requires_human_gate=True`` blocks an automatic promotion."""


class SkillNotFoundError(Exception):
    """Raised when the given skill_id does not exist in the database."""


# ---------------------------------------------------------------------------
# SkillLifecycleManager
# ---------------------------------------------------------------------------

# Every SkillState that is NOT claude_native — used to build "any → claude_native"
_ALL_STATES: tuple[SkillState, ...] = (
    SkillState.CLAUDE_NATIVE,
    SkillState.SKILL_CANDIDATE,
    SkillState.DRAFT,
    SkillState.SANDBOX,
    SkillState.SHADOW_PRIMARY,
    SkillState.TRUSTED,
    SkillState.FLAGGED_FOR_REVIEW,
    SkillState.DEGRADED,
)

# Safety-critical fix (Fable critique #2 / #5): the ``requires_human_gate`` flag
# gates *trust-pipeline promotions* only — a skill advancing into sandbox or
# beyond, where it begins to execute / shadow / serve live. These are the only
# destination states a system actor may not reach when the gate is set.
#
# Everything else is allowed for a system actor:
#   - the draft build-up (``claude_native → skill_candidate → draft``), which
#     merely assembles a candidate and is not a trust promotion; and
#   - safety demotions (``flagged_for_review``, ``degraded``, ``claude_native``
#     via degradation / evolution_failure), which are the §23.4 safety net and
#     must never be blocked by the very flag meant to keep an unproven skill out
#     of production.
_GATED_PROMOTION_STATES: frozenset[SkillState] = frozenset(
    {
        SkillState.SANDBOX,
        SkillState.SHADOW_PRIMARY,
        SkillState.TRUSTED,
    }
)


def _build_transitions() -> dict[tuple[SkillState, SkillState], set[str]]:
    """Build the authoritative transition table from spec §6.2."""
    S = SkillState  # noqa: N806 — single-letter alias keeps table below readable

    table: dict[tuple[SkillState, SkillState], set[str]] = {
        (S.CLAUDE_NATIVE, S.SKILL_CANDIDATE): {"gate_passed", "manual_override"},
        (S.SKILL_CANDIDATE, S.DRAFT): {"gate_passed", "manual_override"},
        (S.DRAFT, S.SANDBOX): {"human_approval", "manual_override"},
        (S.SANDBOX, S.SHADOW_PRIMARY): {"gate_passed", "human_approval", "manual_override"},
        (S.SHADOW_PRIMARY, S.TRUSTED): {"gate_passed", "human_approval", "manual_override"},
        (S.SHADOW_PRIMARY, S.FLAGGED_FOR_REVIEW): {"degradation", "manual_override"},
        (S.TRUSTED, S.FLAGGED_FOR_REVIEW): {"degradation", "manual_override"},
        (S.FLAGGED_FOR_REVIEW, S.TRUSTED): {"human_approval", "manual_override"},
        (S.FLAGGED_FOR_REVIEW, S.DEGRADED): {"human_approval", "manual_override"},
        (S.DEGRADED, S.DRAFT): {"gate_passed", "manual_override"},
        (S.DEGRADED, S.CLAUDE_NATIVE): {"evolution_failed", "manual_override"},
    }

    # "Any state → claude_native" is always allowed with manual_override.
    # The explicit degraded → claude_native row above already has {evolution_failed,
    # manual_override}, so we only add/merge for states that don't have an explicit row.
    for from_state in _ALL_STATES:
        key = (from_state, S.CLAUDE_NATIVE)
        if key in table:
            table[key] = table[key] | {"manual_override"}
        else:
            table[key] = {"manual_override"}

    return table


class SkillLifecycleManager:
    """Sole mutator of ``skill.state`` in the Donna database.

    All state transitions pass through :meth:`transition`, which:

    1. Validates the transition against the spec §6.2 table.
    2. Enforces the ``requires_human_gate`` constraint.
    3. Atomically updates ``skill.state`` and writes an audit row.
    """

    _TRANSITIONS: dict[tuple[SkillState, SkillState], set[str]] = _build_transitions()

    def __init__(self, connection: aiosqlite.Connection, config: SkillSystemConfig) -> None:
        self._conn = connection
        self._config = config
        # Fan-out hook, fired after every successful state transition. Wave 3
        # uses this to keep automation cadences in sync with skill lifecycle.
        self.after_state_change = _AfterStateChangeHook()

    async def transition(
        self,
        skill_id: str,
        to_state: SkillState,
        reason: str,
        actor: str,
        actor_id: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Transition *skill_id* to *to_state*, enforcing all spec rules.

        Parameters
        ----------
        skill_id:
            Primary key of the target skill row.
        to_state:
            The desired destination :class:`~donna.tasks.db_models.SkillState`.
        reason:
            One of ``gate_passed``, ``human_approval``, ``degradation``,
            ``evolution_failed``, ``manual_override``.
        actor:
            ``"system"`` or ``"user"``.
        actor_id:
            Optional external identifier for the actor (e.g. Discord user ID).
        notes:
            Free-text note to store in the audit row.

        Raises
        ------
        SkillNotFoundError
            The *skill_id* does not exist.
        IllegalTransitionError
            The from→to pair is not in the transition table, it is a self-loop,
            or the *reason* is not permitted for that pair.
        HumanGateRequiredError
            ``skill.requires_human_gate`` is ``True`` and *actor* is ``"system"``
            *and* the transition is a promotion (not a safety demotion).
        """
        # 1. Load current skill row.
        cursor = await self._conn.execute(
            "SELECT state, requires_human_gate, capability_name FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise SkillNotFoundError(f"Skill not found: {skill_id!r}")

        current_state_value, requires_human_gate_int, capability_name = row
        current_state = SkillState(current_state_value)
        requires_human_gate = bool(requires_human_gate_int)

        # 2. Self-loop check.
        if to_state == current_state:
            raise IllegalTransitionError(
                f"Self-loop transition not allowed (state={current_state.value!r})"
            )

        # 3. Transition table lookup.
        key = (current_state, to_state)
        allowed_reasons = self._TRANSITIONS.get(key)
        if allowed_reasons is None:
            raise IllegalTransitionError(
                f"Transition {current_state.value!r} → {to_state.value!r} is not permitted."
            )

        # 4. Reason check.
        if reason not in allowed_reasons:
            raise IllegalTransitionError(
                f"Reason {reason!r} is not allowed for transition "
                f"{current_state.value!r} → {to_state.value!r}. "
                f"Allowed: {sorted(allowed_reasons)}"
            )

        # 4b. human_approval is, by definition, a human action. A system actor
        #     may never claim it — that would let automation forge the one
        #     manual gate the §23.5 lifecycle depends on (Fable critique #5).
        if reason == "human_approval" and actor == "system":
            raise IllegalTransitionError(
                f"Reason 'human_approval' requires a non-system actor; "
                f"got actor={actor!r} for transition "
                f"{current_state.value!r} → {to_state.value!r}."
            )

        # 5. Human gate check — trust-pipeline promotions only.
        #    A skill flagged ``requires_human_gate`` must never auto-promote into
        #    sandbox or beyond, but the draft build-up and the safety-net
        #    demotions (degradation / evolution-failure, driven by
        #    ``actor='system'``) must always be allowed to fire, otherwise the
        #    gate blocks the very protection it exists to enable (Fable critique
        #    #2).
        if (
            requires_human_gate
            and actor == "system"
            and to_state in _GATED_PROMOTION_STATES
        ):
            raise HumanGateRequiredError(
                f"Skill {skill_id!r} requires human approval; "
                f"actor='system' may not perform the promotion "
                f"{current_state.value!r} → {to_state.value!r}."
            )

        # 6. Atomic DB update + audit insert.
        now = datetime.now(UTC).isoformat()
        transition_id = str(uuid6.uuid7())

        await self._conn.execute(
            "UPDATE skill SET state = ?, updated_at = ? WHERE id = ?",
            (to_state.value, now, skill_id),
        )
        await self._conn.execute(
            """
            INSERT INTO skill_state_transition
                (id, skill_id, from_state, to_state, reason, actor, actor_id, at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                skill_id,
                current_state.value,
                to_state.value,
                reason,
                actor,
                actor_id,
                now,
                notes,
            ),
        )
        await self._conn.commit()

        logger.info(
            "skill_state_transition",
            skill_id=skill_id,
            from_state=current_state.value,
            to_state=to_state.value,
            reason=reason,
            actor=actor,
            actor_id=actor_id,
        )

        # 7. Fire the after-state-change hook. Subscriber failures are swallowed
        #    inside the hook so they never break the transition.
        await self.after_state_change.fire(capability_name, to_state.value)

    # ---------------------------------------------------------------------------
    # Auto-promotion gates
    # ---------------------------------------------------------------------------

    async def check_and_promote_if_eligible(self, skill_id: str) -> str | None:
        """Check if a skill is eligible for auto-promotion and promote if so.

        Returns the new state value if promoted, None otherwise.

        Only fires for skills in ``sandbox`` or ``shadow_primary`` state. Honors
        ``requires_human_gate`` — logs eligibility but does not transition.
        """
        # 1. Load skill row.
        cursor = await self._conn.execute(
            "SELECT state, requires_human_gate, baseline_agreement, current_version_id "
            "FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        state_value, requires_human_gate_int, _baseline, current_version_id = row
        current_state = SkillState(state_value)
        requires_human_gate = bool(requires_human_gate_int)

        # 2. Route to the appropriate gate.
        if current_state == SkillState.SANDBOX:
            return await self._maybe_promote_to_shadow_primary(
                skill_id=skill_id,
                requires_human_gate=requires_human_gate,
                current_version_id=current_version_id,
            )
        if current_state == SkillState.SHADOW_PRIMARY:
            return await self._maybe_promote_to_trusted(
                skill_id=skill_id,
                requires_human_gate=requires_human_gate,
                current_version_id=current_version_id,
            )
        return None

    async def _maybe_promote_to_shadow_primary(
        self,
        skill_id: str,
        requires_human_gate: bool,
        current_version_id: str | None,
    ) -> str | None:
        """Gate: successful skill_runs >= N AND validity_rate >= threshold.

        validity_rate = fraction of *valid* skill_runs scoped to the current
        version. A run counts as valid only when it both succeeded and recorded
        no degraded step outcome (``continued`` / ``step_failed`` /
        ``skill_failed``) — an ``on_failure: continue`` step must not let a
        run that silently dropped work clear the gate (Fable critique #6).

        The evidence is filtered to ``skill_version_id = current_version_id`` so
        an evolved version cannot inherit its predecessor's track record and
        clear the gate with zero runs of its own code (Fable critique #3).
        """
        config = self._config
        min_runs = config.sandbox_promotion_min_runs
        rate_threshold = config.sandbox_promotion_validity_rate

        if current_version_id is None:
            return None

        cursor = await self._conn.execute(
            "SELECT id, status FROM skill_run "
            "WHERE skill_id = ? AND skill_version_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (skill_id, current_version_id, min_runs),
        )
        run_rows = list(await cursor.fetchall())

        if len(run_rows) < min_runs:
            return None

        valid = 0
        for run_id, status in run_rows:
            if status == "succeeded" and not await self._run_has_degraded_step(run_id):
                valid += 1
        validity_rate = valid / len(run_rows)
        if validity_rate < rate_threshold:
            return None

        if requires_human_gate:
            logger.info(
                "skill_eligible_for_promotion_blocked_by_human_gate",
                skill_id=skill_id,
                from_state="sandbox",
                to_state="shadow_primary",
                validity_rate=validity_rate,
            )
            return None

        notes = json.dumps({"validity_rate": validity_rate, "runs_examined": len(run_rows)})
        await self.transition(
            skill_id=skill_id,
            to_state=SkillState.SHADOW_PRIMARY,
            reason="gate_passed",
            actor="system",
            notes=notes,
        )
        return SkillState.SHADOW_PRIMARY.value

    async def _run_has_degraded_step(self, skill_run_id: str) -> bool:
        """Return ``True`` if any step of *skill_run_id* recorded a degraded outcome.

        A run can finish with ``status='succeeded'`` while individual steps were
        absorbed via ``on_failure: continue`` (recorded as ``continued``) or
        terminally failed (``step_failed`` / ``skill_failed``). Such a run is
        not clean evidence for a promotion gate (Fable critique #6).

        Args:
            skill_run_id: The skill_run row to inspect.

        Returns:
            ``True`` if at least one step result has a degraded validation
            status, else ``False``.
        """
        cursor = await self._conn.execute(
            "SELECT 1 FROM skill_step_result "
            "WHERE skill_run_id = ? "
            "AND validation_status IN ('continued', 'step_failed', 'skill_failed') "
            "LIMIT 1",
            (skill_run_id,),
        )
        return await cursor.fetchone() is not None

    async def _maybe_promote_to_trusted(
        self,
        skill_id: str,
        requires_human_gate: bool,
        current_version_id: str | None,
    ) -> str | None:
        """Gate: divergences >= M AND agreement_rate >= threshold AND failure-rate ceiling.

        agreement_rate = mean overall_agreement on the rolling window.
        On promotion, set baseline_agreement to the observed rate.

        Evidence is scoped to ``skill_version_id = current_version_id`` so an
        evolved version does not inherit its predecessor's divergence history
        (Fable critique #3). The run-level failure rate over the same window
        must also stay at or below
        ``config.shadow_primary_promotion_max_failure_rate`` — a high agreement
        score must not mask a skill that frequently fails outright (Fable
        critique #6).
        """
        config = self._config
        min_runs = config.shadow_primary_promotion_min_runs
        rate_threshold = config.shadow_primary_promotion_agreement_rate
        max_failure_rate = config.shadow_primary_promotion_max_failure_rate

        if current_version_id is None:
            return None

        cursor = await self._conn.execute(
            """
            SELECT d.overall_agreement, r.status
              FROM skill_divergence d
              JOIN skill_run r ON d.skill_run_id = r.id
             WHERE r.skill_id = ? AND r.skill_version_id = ?
             ORDER BY d.created_at DESC
             LIMIT ?
            """,
            (skill_id, current_version_id, min_runs),
        )
        window = list(await cursor.fetchall())
        agreements = [row[0] for row in window]

        if len(agreements) < min_runs:
            return None

        failures = sum(1 for row in window if row[1] != "succeeded")
        failure_rate = failures / len(window)
        if failure_rate > max_failure_rate:
            logger.info(
                "skill_promotion_blocked_by_failure_rate",
                skill_id=skill_id,
                from_state="shadow_primary",
                to_state="trusted",
                failure_rate=failure_rate,
                ceiling=max_failure_rate,
            )
            return None

        mean_agreement = sum(agreements) / len(agreements)
        if mean_agreement < rate_threshold:
            return None

        if requires_human_gate:
            logger.info(
                "skill_eligible_for_promotion_blocked_by_human_gate",
                skill_id=skill_id,
                from_state="shadow_primary",
                to_state="trusted",
                agreement_rate=mean_agreement,
            )
            return None

        notes = json.dumps(
            {
                "mean_agreement": mean_agreement,
                "runs_examined": len(agreements),
            }
        )
        await self.transition(
            skill_id=skill_id,
            to_state=SkillState.TRUSTED,
            reason="gate_passed",
            actor="system",
            notes=notes,
        )
        # Also update baseline_agreement on the skill row.
        await self._conn.execute(
            "UPDATE skill SET baseline_agreement = ? WHERE id = ?",
            (mean_agreement, skill_id),
        )
        await self._conn.commit()
        return SkillState.TRUSTED.value
