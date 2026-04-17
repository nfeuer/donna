"""assemble_skill_system — wires up all Phase 3 + 4 skill-system components.

Invoked from the FastAPI lifespan to hydrate app.state with ready-to-use
components. Respects config.enabled — when false, returns None and the lifespan
should not register any background tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiosqlite
import structlog

from donna.config import SkillSystemConfig
from donna.skills.auto_drafter import AutoDrafter
from donna.skills.candidate_report import SkillCandidateRepository
from donna.skills.correction_cluster import CorrectionClusterDetector
from donna.skills.degradation import DegradationDetector
from donna.skills.detector import SkillCandidateDetector
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.equivalence import EquivalenceJudge
from donna.skills.evolution import Evolver
from donna.skills.evolution_scheduler import EvolutionScheduler
from donna.skills.lifecycle import SkillLifecycleManager
from donna.skills.shadow import ShadowSampler

logger = structlog.get_logger()


@dataclass(slots=True)
class SkillSystemBundle:
    config: SkillSystemConfig
    lifecycle_manager: SkillLifecycleManager
    divergence_repo: SkillDivergenceRepository
    candidate_repo: SkillCandidateRepository
    judge: EquivalenceJudge
    shadow_sampler: ShadowSampler
    detector: SkillCandidateDetector
    auto_drafter: AutoDrafter
    degradation: DegradationDetector
    evolver: Evolver
    evolution_scheduler: EvolutionScheduler
    correction_cluster: CorrectionClusterDetector


def assemble_skill_system(
    connection: aiosqlite.Connection,
    model_router: Any,
    budget_guard: Any,
    notifier: Callable[[str], Awaitable[None]],
    config: SkillSystemConfig,
    validation_executor_factory: Callable[[], Any] | None = None,
) -> SkillSystemBundle | None:
    """Wire all Phase 3 + 4 skill-system components. Returns None if disabled."""
    if not config.enabled:
        logger.info("skill_system_disabled", enabled=False)
        return None

    # Default factory: real ValidationExecutor with the current router + config.
    if validation_executor_factory is None:
        from donna.skills.validation_executor import ValidationExecutor

        def _default_validation_executor_factory() -> ValidationExecutor:
            return ValidationExecutor(model_router=model_router, config=config)

        validation_executor_factory = _default_validation_executor_factory

    lifecycle = SkillLifecycleManager(connection, config)
    divergence_repo = SkillDivergenceRepository(connection)
    candidate_repo = SkillCandidateRepository(connection)
    judge = EquivalenceJudge(model_router)

    shadow_sampler = ShadowSampler(
        model_router=model_router,
        judge=judge,
        divergence_repo=divergence_repo,
        config=config,
        lifecycle_manager=lifecycle,
    )
    detector = SkillCandidateDetector(connection, candidate_repo, config)
    auto_drafter = AutoDrafter(
        connection=connection,
        model_router=model_router,
        budget_guard=budget_guard,
        candidate_repo=candidate_repo,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=validation_executor_factory,
    )
    degradation = DegradationDetector(
        connection=connection,
        divergence_repo=divergence_repo,
        lifecycle_manager=lifecycle,
        config=config,
    )
    evolver = Evolver(
        connection=connection,
        model_router=model_router,
        budget_guard=budget_guard,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=validation_executor_factory,
    )
    evolution_scheduler = EvolutionScheduler(
        connection=connection, evolver=evolver, config=config,
    )
    correction_cluster = CorrectionClusterDetector(
        connection=connection,
        lifecycle_manager=lifecycle,
        notifier=notifier,
        config=config,
    )

    logger.info(
        "skill_system_bundle_assembled",
        match_high=config.match_confidence_high,
        shadow_sample_rate_trusted=config.shadow_sample_rate_trusted,
        evolution_daily_cap=config.evolution_daily_cap,
    )

    return SkillSystemBundle(
        config=config,
        lifecycle_manager=lifecycle,
        divergence_repo=divergence_repo,
        candidate_repo=candidate_repo,
        judge=judge,
        shadow_sampler=shadow_sampler,
        detector=detector,
        auto_drafter=auto_drafter,
        degradation=degradation,
        evolver=evolver,
        evolution_scheduler=evolution_scheduler,
        correction_cluster=correction_cluster,
    )
