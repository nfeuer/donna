"""ShadowSampler — fire-and-forget shadow Claude calls for skill divergence tracking.

After a successful skill execution in shadow_primary or trusted state, the sampler
runs the equivalent Claude path in the background, compares outputs via
EquivalenceJudge, and writes a skill_divergence row. Never blocks the caller;
never raises.

See Phase 3 plan Task 6 and docs/skills-system.md.
"""

from __future__ import annotations

import random as _random_module
import uuid
from typing import Any, Callable

import structlog

from donna.config import SkillSystemConfig
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.equivalence import EquivalenceJudge
from donna.skills.models import SkillRow
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()


class ShadowSampler:
    """Runs a shadow Claude invocation after a successful skill execution.

    Decides whether to sample based on the skill's current state and the
    configured sampling rate. Writes a skill_divergence row with the
    agreement score from the EquivalenceJudge.

    All methods catch and log every exception — callers are guaranteed no
    unhandled raise from this class.
    """

    def __init__(
        self,
        model_router: Any,
        judge: EquivalenceJudge,
        divergence_repo: SkillDivergenceRepository,
        config: SkillSystemConfig,
        # Deterministic random: used to inject a fixed seed in tests.
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        self._router = model_router
        self._judge = judge
        self._divergence_repo = divergence_repo
        self._config = config
        self._random_fn = random_fn if random_fn is not None else _random_module.random

    async def sample_if_applicable(
        self,
        skill: SkillRow,
        skill_run_id: str,
        inputs: dict,
        skill_output: dict,
        claude_task_type: str,
        claude_prompt: str,
    ) -> None:
        """Run a shadow Claude invocation if applicable; write divergence row.

        This method is designed to be called via asyncio.create_task — it
        returns immediately from the caller's perspective and never raises.
        """
        try:
            await self._do_sample(
                skill=skill,
                skill_run_id=skill_run_id,
                inputs=inputs,
                skill_output=skill_output,
                claude_task_type=claude_task_type,
                claude_prompt=claude_prompt,
            )
        except Exception:
            logger.exception(
                "shadow_sampler_unexpected_error",
                skill_id=skill.id,
                skill_run_id=skill_run_id,
            )

    async def _do_sample(
        self,
        skill: SkillRow,
        skill_run_id: str,
        inputs: dict,
        skill_output: dict,
        claude_task_type: str,
        claude_prompt: str,
    ) -> None:
        """Core sampling logic; called inside the outer try/except."""
        # Step 1: Decide whether to sample based on skill state.
        state = skill.state
        if state == SkillState.SHADOW_PRIMARY:
            rate = 1.0
        elif state == SkillState.TRUSTED:
            rate = self._config.shadow_sample_rate_trusted
        else:
            # sandbox, draft, degraded, etc. — never sample.
            return

        # Step 2: Apply sampling rate.
        if self._random_fn() >= rate:
            return

        # Step 3: Run the Claude path.
        invocation_id: str
        claude_output: dict
        try:
            parsed, metadata = await self._router.complete(
                prompt=claude_prompt,
                task_type=claude_task_type,
                task_id=None,
                user_id="system",
            )
            claude_output = parsed if isinstance(parsed, dict) else {"output": parsed}
            # CompletionMetadata does not expose invocation_id; fall back to uuid7.
            invocation_id = getattr(metadata, "invocation_id", None) or str(uuid.uuid4())
        except Exception:
            logger.warning(
                "shadow_sample_claude_call_failed",
                skill_id=skill.id,
                skill_run_id=skill_run_id,
                claude_task_type=claude_task_type,
            )
            return

        # Step 4 & 5: Ask EquivalenceJudge for agreement score.
        agreement: float
        try:
            agreement = await self._judge.judge(
                output_a=skill_output,
                output_b=claude_output,
                context={"capability": skill.capability_name},
            )
        except Exception:
            logger.warning(
                "shadow_sample_judge_failed",
                skill_id=skill.id,
                skill_run_id=skill_run_id,
            )
            agreement = 0.0
            # Fall through to write the divergence row with 0.0 agreement.

        # Step 6: Build diff summary.
        diff_summary = {
            "skill_output": skill_output,
            "claude_output": claude_output,
        }

        # Step 7: Write the divergence row.
        await self._divergence_repo.record(
            skill_run_id=skill_run_id,
            shadow_invocation_id=invocation_id,
            overall_agreement=agreement,
            diff_summary=diff_summary,
        )

        # Step 8: Log success.
        logger.info(
            "shadow_sample_recorded",
            skill_id=skill.id,
            skill_run_id=skill_run_id,
            agreement=agreement,
        )
