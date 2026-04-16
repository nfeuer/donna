"""Nightly cron orchestrator for Phase 4 skill-system housekeeping.

Runs five sequential steps:
1. Detect new skill candidates (SkillCandidateDetector).
2. Run evolution on eligible skills (EvolutionScheduler) — before auto-drafting
   per spec §6.5 budget ordering.
3. Auto-draft top candidates within remaining daily budget (AutoDrafter).
4. Run degradation detection on all trusted skills (DegradationDetector).
5. Scan for correction clusters (CorrectionCluster).

Failures in any step are caught and recorded; remaining steps continue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(slots=True)
class NightlyDeps:
    """Dependencies injected into the nightly cron entry point.

    ``daily_budget_limit_usd`` is passed directly so the caller controls
    the threshold without requiring access to BudgetGuard internals.
    """

    detector: Any           # SkillCandidateDetector
    auto_drafter: Any       # AutoDrafter
    degradation: Any        # DegradationDetector
    evolution_scheduler: Any  # EvolutionScheduler
    correction_cluster: Any   # CorrectionCluster
    cost_tracker: Any       # CostTracker (for get_daily_cost)
    daily_budget_limit_usd: float
    config: Any             # SkillSystemConfig


@dataclass(slots=True)
class NightlyReport:
    """Structured summary of a single nightly run."""

    started_at: str                                                    # ISO timestamp
    finished_at: str                                                   # ISO timestamp
    new_candidates: list[str] = field(default_factory=list)            # candidate IDs
    drafted: list[dict] = field(default_factory=list)                  # AutoDraftReport as dicts
    evolved: list[dict] = field(default_factory=list)                  # EvolutionReport as dicts
    correction_flagged: list[dict] = field(default_factory=list)       # correction-cluster hits
    degraded: list[dict] = field(default_factory=list)                 # DegradationReport as dicts
    errors: list[dict] = field(default_factory=list)                   # [{"step": "...", "error": "..."}]


async def run_nightly_tasks(deps: NightlyDeps) -> NightlyReport:
    """Execute all nightly skill-system housekeeping steps.

    Each step is wrapped in try/except so a failure in one step does not
    prevent the remaining steps from running.

    Args:
        deps: All collaborators and configuration needed for the run.

    Returns:
        A :class:`NightlyReport` with counts and error details.
    """
    started = datetime.now(timezone.utc).isoformat()
    report = NightlyReport(started_at=started, finished_at="")

    if not deps.config.enabled:
        logger.info("nightly_tasks_skipped_disabled")
        report.finished_at = datetime.now(timezone.utc).isoformat()
        return report

    # Step 1: Detect new skill candidates.
    try:
        report.new_candidates = await deps.detector.run()
        logger.info("nightly_detector_done", new_candidates=len(report.new_candidates))
    except Exception as exc:
        report.errors.append({"step": "detector", "error": str(exc)})
        logger.exception("nightly_detector_failed")

    # Compute remaining budget (shared across steps 2 and 3).
    try:
        daily_summary = await deps.cost_tracker.get_daily_cost()
        daily_spent = daily_summary.total_usd
        remaining_budget = max(0.0, deps.daily_budget_limit_usd - daily_spent)
    except Exception as exc:
        report.errors.append({"step": "cost_tracker", "error": str(exc)})
        logger.exception("nightly_cost_tracker_failed")
        remaining_budget = 0.0

    # Step 2: Evolution — must run before auto-drafting (spec §6.5 budget ordering).
    try:
        evo_reports = await deps.evolution_scheduler.run(remaining_budget_usd=remaining_budget)
        report.evolved = [_as_dict(r) for r in evo_reports]
        # Decrement remaining budget by approx cost per attempt.
        per_cost = deps.config.evolution_estimated_cost_usd
        remaining_budget = max(0.0, remaining_budget - per_cost * len(evo_reports))
        logger.info("nightly_evolution_done", evolved=len(report.evolved))
    except Exception as exc:
        report.errors.append({"step": "evolution_scheduler", "error": str(exc)})
        logger.exception("nightly_evolution_failed")

    # Step 3: Auto-draft top candidates within remaining daily budget.
    try:
        reports = await deps.auto_drafter.run(
            remaining_budget_usd=remaining_budget,
            max_drafts=deps.config.auto_draft_daily_cap,
        )
        report.drafted = [_as_dict(r) for r in reports]
        logger.info("nightly_drafter_done", drafts=len(report.drafted))
    except Exception as exc:
        report.errors.append({"step": "auto_drafter", "error": str(exc)})
        logger.exception("nightly_drafter_failed")

    # Step 4: Degradation detection on all trusted skills.
    try:
        reports = await deps.degradation.run()
        report.degraded = [_as_dict(r) for r in reports]
        logger.info("nightly_degradation_done", evaluated=len(report.degraded))
    except Exception as exc:
        report.errors.append({"step": "degradation", "error": str(exc)})
        logger.exception("nightly_degradation_failed")

    # Step 5: Correction cluster scan.
    try:
        flagged = await deps.correction_cluster.scan_once()
        report.correction_flagged = flagged
        logger.info("nightly_correction_cluster_done", flagged=len(flagged))
    except Exception as exc:
        report.errors.append({"step": "correction_cluster", "error": str(exc)})
        logger.exception("nightly_correction_cluster_failed")

    report.finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "nightly_tasks_completed",
        new_candidates=len(report.new_candidates),
        evolved=len(report.evolved),
        drafted=len(report.drafted),
        degraded_evaluated=len(report.degraded),
        correction_flagged=len(report.correction_flagged),
        error_count=len(report.errors),
    )
    return report


def _as_dict(obj: Any) -> dict:
    """Serialize a dataclass instance or plain dict to a dict."""
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    return {"raw": str(obj)}
