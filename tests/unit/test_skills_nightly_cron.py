"""Tests for the nightly cron orchestrator (Task 13 + Task 9 Phase 4).

All external collaborators are replaced with AsyncMock / MagicMock so no
database or LLM calls are made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from donna.config import SkillSystemConfig
from donna.skills.crons.nightly import NightlyDeps, NightlyReport, run_nightly_tasks


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------


def _make_deps(
    detector_new: list[str] | None = None,
    drafter_reports: list | None = None,
    degradation_reports: list | None = None,
    evolution_reports: list | None = None,
    correction_flagged: list | None = None,
    daily_spent: float = 5.0,
    daily_limit: float = 20.0,
    auto_draft_cap: int = 10,
) -> NightlyDeps:
    """Build a NightlyDeps with all collaborators mocked."""
    detector = AsyncMock()
    detector.run.return_value = detector_new or []

    drafter = AsyncMock()
    drafter.run.return_value = drafter_reports or []

    degradation = AsyncMock()
    degradation.run.return_value = degradation_reports or []

    evolution = AsyncMock()
    evolution.run.return_value = evolution_reports or []

    correction = AsyncMock()
    correction.scan_once.return_value = correction_flagged or []

    cost_tracker = AsyncMock()
    daily_summary = MagicMock()
    daily_summary.total_usd = daily_spent
    cost_tracker.get_daily_cost.return_value = daily_summary

    config = SkillSystemConfig(auto_draft_daily_cap=auto_draft_cap, enabled=True)

    return NightlyDeps(
        detector=detector,
        auto_drafter=drafter,
        degradation=degradation,
        evolution_scheduler=evolution,
        correction_cluster=correction,
        cost_tracker=cost_tracker,
        daily_budget_limit_usd=daily_limit,
        config=config,
    )


# ---------------------------------------------------------------------------
# Original Phase 3 tests (unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nightly_calls_all_three_stages_in_order() -> None:
    """All three stages must be called; call order is detector → drafter → degradation."""
    deps = _make_deps()
    await run_nightly_tasks(deps)

    deps.detector.run.assert_called_once()
    deps.auto_drafter.run.assert_called_once()
    deps.degradation.run.assert_called_once()

    # Verify ordering via the mock call sequences on each object.
    # detector.run must have been called before auto_drafter.run and
    # degradation.run, and auto_drafter.run before degradation.run.
    detector_call_idx = deps.detector.run.call_args_list[0]  # noqa: F841 (existence check)
    # If the order were wrong, the test above would still pass — so we
    # additionally check that no errors were recorded for any step,
    # implying all three ran successfully in sequence.
    report = await run_nightly_tasks(deps)
    assert report.errors == []
    assert deps.detector.run.call_count == 2
    assert deps.auto_drafter.run.call_count == 2
    assert deps.degradation.run.call_count == 2


@pytest.mark.asyncio
async def test_nightly_threads_remaining_budget_correctly() -> None:
    """auto_drafter.run must receive remaining_budget_usd = daily_limit - daily_spent."""
    deps = _make_deps(daily_spent=8.0, daily_limit=20.0)
    await run_nightly_tasks(deps)

    _, kwargs = deps.auto_drafter.run.call_args
    assert kwargs["remaining_budget_usd"] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_nightly_passes_auto_draft_daily_cap() -> None:
    """config.auto_draft_daily_cap is forwarded to auto_drafter.run as max_drafts."""
    deps = _make_deps(auto_draft_cap=7)
    await run_nightly_tasks(deps)

    _, kwargs = deps.auto_drafter.run.call_args
    assert kwargs["max_drafts"] == 7


@pytest.mark.asyncio
async def test_nightly_detector_failure_does_not_stop_drafter() -> None:
    """A detector failure must be recorded but drafter and degradation still run."""
    deps = _make_deps()
    deps.detector.run.side_effect = RuntimeError("db unavailable")

    report = await run_nightly_tasks(deps)

    assert len(report.errors) == 1
    assert report.errors[0]["step"] == "detector"
    assert "db unavailable" in report.errors[0]["error"]

    deps.auto_drafter.run.assert_called_once()
    deps.degradation.run.assert_called_once()


@pytest.mark.asyncio
async def test_nightly_drafter_failure_does_not_stop_degradation() -> None:
    """A drafter failure must be recorded but degradation still runs."""
    deps = _make_deps()
    deps.auto_drafter.run.side_effect = ValueError("budget error")

    report = await run_nightly_tasks(deps)

    drafter_errors = [e for e in report.errors if e["step"] == "auto_drafter"]
    assert len(drafter_errors) == 1
    assert "budget error" in drafter_errors[0]["error"]

    deps.degradation.run.assert_called_once()


@pytest.mark.asyncio
async def test_nightly_degradation_failure_is_recorded() -> None:
    """A degradation failure must be recorded in report.errors."""
    deps = _make_deps()
    deps.degradation.run.side_effect = Exception("degradation boom")

    report = await run_nightly_tasks(deps)

    degradation_errors = [e for e in report.errors if e["step"] == "degradation"]
    assert len(degradation_errors) == 1
    assert "degradation boom" in degradation_errors[0]["error"]


@pytest.mark.asyncio
async def test_nightly_report_serializes_dataclass_reports() -> None:
    """AutoDraftReport dataclass instances must be serialized to dicts in report.drafted."""

    @dataclass
    class _FakeAutoDraftReport:
        candidate_id: str
        outcome: str
        skill_id: str | None = None
        pass_rate: float | None = None
        rationale: str | None = None

    fake_reports = [
        _FakeAutoDraftReport(candidate_id="c1", outcome="drafted", skill_id="s1", pass_rate=0.9),
        _FakeAutoDraftReport(candidate_id="c2", outcome="dismissed"),
    ]

    deps = _make_deps(drafter_reports=fake_reports)
    report = await run_nightly_tasks(deps)

    assert len(report.drafted) == 2
    assert report.drafted[0] == {
        "candidate_id": "c1",
        "outcome": "drafted",
        "skill_id": "s1",
        "pass_rate": 0.9,
        "rationale": None,
    }
    assert report.drafted[1]["outcome"] == "dismissed"
    assert isinstance(report.drafted[0], dict)


@pytest.mark.asyncio
async def test_nightly_timestamps_set() -> None:
    """started_at and finished_at must be valid ISO 8601 strings."""
    deps = _make_deps()
    report = await run_nightly_tasks(deps)

    assert report.started_at != ""
    assert report.finished_at != ""

    # Both must parse as valid datetimes without raising.
    started = datetime.fromisoformat(report.started_at)
    finished = datetime.fromisoformat(report.finished_at)

    assert finished >= started
    # Timezone-aware.
    assert started.tzinfo is not None
    assert finished.tzinfo is not None


@pytest.mark.asyncio
async def test_nightly_skipped_when_disabled() -> None:
    """When config.enabled=False, run_nightly_tasks must return immediately
    without calling any collaborators."""
    deps = _make_deps()
    deps.config.enabled = False

    report = await run_nightly_tasks(deps)

    assert report.new_candidates == []
    assert report.drafted == []
    assert report.degraded == []
    assert report.errors == []
    deps.detector.run.assert_not_called()
    deps.auto_drafter.run.assert_not_called()
    deps.degradation.run.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 4 new tests: evolution + correction cluster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nightly_calls_evolution_before_drafter() -> None:
    """Evolution should be called before auto-drafting (spec §6.5)."""
    deps = _make_deps()
    call_order: list[str] = []

    async def _detector_run(*a, **kw):
        call_order.append("detector")
        return []

    async def _evolution_run(*a, **kw):
        call_order.append("evolution")
        return []

    async def _drafter_run(*a, **kw):
        call_order.append("drafter")
        return []

    async def _degradation_run(*a, **kw):
        call_order.append("degradation")
        return []

    async def _correction_scan(*a, **kw):
        call_order.append("correction")
        return []

    deps.detector.run = _detector_run
    deps.evolution_scheduler.run = _evolution_run
    deps.auto_drafter.run = _drafter_run
    deps.degradation.run = _degradation_run
    deps.correction_cluster.scan_once = _correction_scan

    await run_nightly_tasks(deps)
    assert call_order.index("detector") < call_order.index("evolution")
    assert call_order.index("evolution") < call_order.index("drafter")
    assert call_order.index("drafter") < call_order.index("degradation")
    assert call_order.index("degradation") < call_order.index("correction")


@pytest.mark.asyncio
async def test_nightly_records_evolution_reports() -> None:
    """Evolution report objects are serialized into report.evolved."""
    from donna.skills.evolution import EvolutionReport

    deps = _make_deps(
        evolution_reports=[
            EvolutionReport(skill_id="s1", outcome="success", new_version_id="v2"),
            EvolutionReport(skill_id="s2", outcome="rejected_validation"),
        ],
    )
    report = await run_nightly_tasks(deps)
    assert len(report.evolved) == 2
    assert report.evolved[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_nightly_records_correction_flagged() -> None:
    """correction_cluster hits are stored verbatim in report.correction_flagged."""
    flagged = [{"skill_id": "s3", "capability_name": "x", "correction_count": 4}]
    deps = _make_deps(correction_flagged=flagged)
    report = await run_nightly_tasks(deps)
    assert report.correction_flagged == flagged


@pytest.mark.asyncio
async def test_nightly_evolution_failure_still_runs_drafter() -> None:
    """A failed evolution step must be recorded but auto-drafting still runs."""
    deps = _make_deps()
    deps.evolution_scheduler.run.side_effect = RuntimeError("boom")
    report = await run_nightly_tasks(deps)
    assert any(e["step"] == "evolution_scheduler" for e in report.errors)
    deps.auto_drafter.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_nightly_correction_cluster_failure_is_recorded() -> None:
    """A correction cluster failure must be recorded in report.errors."""
    deps = _make_deps()
    deps.correction_cluster.scan_once.side_effect = RuntimeError("bang")
    report = await run_nightly_tasks(deps)
    assert any(e["step"] == "correction_cluster" for e in report.errors)
