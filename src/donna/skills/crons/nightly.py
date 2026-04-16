"""Nightly cron orchestrator for Phase 3 skill-system housekeeping.

Runs three sequential steps:
1. Detect new skill candidates (SkillCandidateDetector).
2. Auto-draft top candidates within remaining daily budget (AutoDrafter).
3. Run degradation detection on all trusted skills (DegradationDetector).

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
    cost_tracker: Any       # CostTracker (for get_daily_cost)
    daily_budget_limit_usd: float
    config: Any             # SkillSystemConfig


@dataclass(slots=True)
class NightlyReport:
    """Structured summary of a single nightly run."""

    started_at: str                                         # ISO timestamp
    finished_at: str                                        # ISO timestamp
    new_candidates: list[str] = field(default_factory=list)  # candidate IDs
    drafted: list[dict] = field(default_factory=list)        # AutoDraftReport as dicts
    degraded: list[dict] = field(default_factory=list)       # DegradationReport as dicts
    errors: list[dict] = field(default_factory=list)         # [{"step": "...", "error": "..."}]


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

    # Step 1: Detect new skill candidates.
    try:
        report.new_candidates = await deps.detector.run()
        logger.info("nightly_detector_done", new_candidates=len(report.new_candidates))
    except Exception as exc:
        report.errors.append({"step": "detector", "error": str(exc)})
        logger.exception("nightly_detector_failed")

    # Step 2: Auto-draft top candidates within remaining daily budget.
    try:
        daily_summary = await deps.cost_tracker.get_daily_cost()
        daily_spent = daily_summary.total_usd
        remaining_budget = max(0.0, deps.daily_budget_limit_usd - daily_spent)

        reports = await deps.auto_drafter.run(
            remaining_budget_usd=remaining_budget,
            max_drafts=deps.config.auto_draft_daily_cap,
        )
        report.drafted = [_as_dict(r) for r in reports]
        logger.info("nightly_drafter_done", drafts=len(report.drafted))
    except Exception as exc:
        report.errors.append({"step": "auto_drafter", "error": str(exc)})
        logger.exception("nightly_drafter_failed")

    # Step 3: Degradation detection on all trusted skills.
    try:
        reports = await deps.degradation.run()
        report.degraded = [_as_dict(r) for r in reports]
        logger.info("nightly_degradation_done", evaluated=len(report.degraded))
    except Exception as exc:
        report.errors.append({"step": "degradation", "error": str(exc)})
        logger.exception("nightly_degradation_failed")

    report.finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "nightly_tasks_completed",
        new_candidates=len(report.new_candidates),
        drafted=len(report.drafted),
        degraded_evaluated=len(report.degraded),
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
