"""SkillLifecycleManager — sole mutator of skill.state.

Enforces all state-machine transitions defined in docs/skills-system.md §6.2.
Every successful state change writes a ``skill_state_transition`` audit row.
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import structlog
import uuid6

from donna.tasks.db_models import SkillState

logger = structlog.get_logger()


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


def _build_transitions() -> dict[tuple[SkillState, SkillState], set[str]]:
    """Build the authoritative transition table from spec §6.2."""
    S = SkillState

    table: dict[tuple[SkillState, SkillState], set[str]] = {
        (S.CLAUDE_NATIVE, S.SKILL_CANDIDATE): {"gate_passed", "manual_override"},
        (S.SKILL_CANDIDATE, S.DRAFT): {"gate_passed", "manual_override"},
        (S.DRAFT, S.SANDBOX): {"human_approval", "manual_override"},
        (S.SANDBOX, S.SHADOW_PRIMARY): {"gate_passed", "human_approval", "manual_override"},
        (S.SHADOW_PRIMARY, S.TRUSTED): {"gate_passed", "human_approval", "manual_override"},
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

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

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
            ``skill.requires_human_gate`` is ``True``, *actor* is ``"system"``,
            and *reason* is not ``"manual_override"``.
        """
        # 1. Load current skill row.
        cursor = await self._conn.execute(
            "SELECT state, requires_human_gate FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise SkillNotFoundError(f"Skill not found: {skill_id!r}")

        current_state_value, requires_human_gate_int = row
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

        # 5. Human gate check.
        if requires_human_gate and actor == "system" and reason != "manual_override":
            raise HumanGateRequiredError(
                f"Skill {skill_id!r} requires human approval; "
                f"actor='system' may not perform automatic promotion "
                f"(reason={reason!r})."
            )

        # 6. Atomic DB update + audit insert.
        now = datetime.now(timezone.utc).isoformat()
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
