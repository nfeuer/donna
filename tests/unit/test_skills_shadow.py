"""Unit tests for ShadowSampler (Phase 3 Task 6).

These tests use unittest.mock to avoid any I/O.  The ShadowSampler must:
  - Never raise.
  - Skip sampling for states outside shadow_primary / trusted.
  - Respect the configured sampling rate for trusted skills.
  - Log and return when the router fails; still write 0.0 agreement when
    only the judge fails.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.config import SkillSystemConfig
from donna.skills.models import SkillRow
from donna.skills.shadow import ShadowSampler
from donna.tasks.db_models import SkillState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(state: SkillState, capability_name: str = "parse_task") -> SkillRow:
    return SkillRow(
        id="s1",
        capability_name=capability_name,
        current_version_id="v1",
        state=state,
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_sampler(
    router=None,
    judge=None,
    repo=None,
    config=None,
    random_fn=None,
) -> ShadowSampler:
    if router is None:
        router = AsyncMock()
        router.complete.return_value = ({"result": "claude"}, MagicMock())
    if judge is None:
        judge = AsyncMock()
        judge.judge.return_value = 0.9
    if repo is None:
        repo = AsyncMock()
        repo.record.return_value = "div-1"
    if config is None:
        config = SkillSystemConfig(shadow_sample_rate_trusted=0.1)
    return ShadowSampler(
        model_router=router,
        judge=judge,
        divergence_repo=repo,
        config=config,
        random_fn=random_fn,
    )


_COMMON_KWARGS = dict(
    skill_run_id="run-1",
    inputs={"raw_text": "hello"},
    skill_output={"title": "Hello"},
    claude_task_type="parse_task",
    claude_prompt='{"capability": "parse_task", "inputs": {"raw_text": "hello"}}',
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sample_skipped_for_sandbox():
    """sandbox state → router.complete never called, repo.record never called."""
    router = AsyncMock()
    repo = AsyncMock()
    sampler = _make_sampler(router=router, repo=repo)

    await sampler.sample_if_applicable(
        skill=_make_skill(SkillState.SANDBOX),
        **_COMMON_KWARGS,
    )

    router.complete.assert_not_called()
    repo.record.assert_not_called()


@pytest.mark.asyncio
async def test_sample_fires_for_shadow_primary():
    """shadow_primary state → router.complete called once, repo.record called once."""
    router = AsyncMock()
    router.complete.return_value = ({"result": "claude"}, MagicMock())

    judge = AsyncMock()
    judge.judge.return_value = 0.85

    repo = AsyncMock()
    repo.record.return_value = "div-1"

    sampler = _make_sampler(router=router, judge=judge, repo=repo, random_fn=lambda: 0.0)

    await sampler.sample_if_applicable(
        skill=_make_skill(SkillState.SHADOW_PRIMARY),
        **_COMMON_KWARGS,
    )

    router.complete.assert_called_once()
    judge.judge.assert_called_once()
    repo.record.assert_called_once()

    call_kwargs = repo.record.call_args.kwargs
    assert call_kwargs["overall_agreement"] == 0.85
    assert call_kwargs["skill_run_id"] == "run-1"


@pytest.mark.asyncio
async def test_sample_respects_trusted_rate_samples():
    """trusted state, rate=0.1, random=0.05 (< 0.1) → should sample."""
    router = AsyncMock()
    router.complete.return_value = ({"result": "claude"}, MagicMock())
    repo = AsyncMock()

    config = SkillSystemConfig(shadow_sample_rate_trusted=0.1)
    sampler = _make_sampler(
        router=router, repo=repo, config=config, random_fn=lambda: 0.05
    )

    await sampler.sample_if_applicable(
        skill=_make_skill(SkillState.TRUSTED),
        **_COMMON_KWARGS,
    )

    router.complete.assert_called_once()
    repo.record.assert_called_once()


@pytest.mark.asyncio
async def test_sample_respects_trusted_rate_skips():
    """trusted state, rate=0.1, random=0.5 (>= 0.1) → should skip."""
    router = AsyncMock()
    repo = AsyncMock()

    config = SkillSystemConfig(shadow_sample_rate_trusted=0.1)
    sampler = _make_sampler(
        router=router, repo=repo, config=config, random_fn=lambda: 0.5
    )

    await sampler.sample_if_applicable(
        skill=_make_skill(SkillState.TRUSTED),
        **_COMMON_KWARGS,
    )

    router.complete.assert_not_called()
    repo.record.assert_not_called()


@pytest.mark.asyncio
async def test_sample_logs_and_returns_on_router_failure():
    """router.complete raises → no repo.record call, logs shadow_sample_claude_call_failed."""
    router = AsyncMock()
    router.complete.side_effect = RuntimeError("connection refused")

    repo = AsyncMock()

    sampler = _make_sampler(router=router, repo=repo, random_fn=lambda: 0.0)

    with patch("donna.skills.shadow.logger") as mock_logger:
        await sampler.sample_if_applicable(
            skill=_make_skill(SkillState.SHADOW_PRIMARY),
            **_COMMON_KWARGS,
        )

    repo.record.assert_not_called()

    # Verify warning log was emitted for the router failure.
    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("shadow_sample_claude_call_failed" in c for c in warning_calls)


@pytest.mark.asyncio
async def test_sample_writes_zero_agreement_when_judge_fails():
    """judge.judge raises → repo.record still called with overall_agreement=0.0."""
    router = AsyncMock()
    router.complete.return_value = ({"result": "claude"}, MagicMock())

    judge = AsyncMock()
    judge.judge.side_effect = RuntimeError("judge exploded")

    repo = AsyncMock()
    repo.record.return_value = "div-1"

    sampler = _make_sampler(
        router=router, judge=judge, repo=repo, random_fn=lambda: 0.0
    )

    await sampler.sample_if_applicable(
        skill=_make_skill(SkillState.SHADOW_PRIMARY),
        **_COMMON_KWARGS,
    )

    repo.record.assert_called_once()
    call_kwargs = repo.record.call_args.kwargs
    assert call_kwargs["overall_agreement"] == 0.0


@pytest.mark.asyncio
async def test_sample_never_raises_on_unexpected_error():
    """repo.record raises → the public call does NOT propagate the exception."""
    router = AsyncMock()
    router.complete.return_value = ({"result": "claude"}, MagicMock())

    judge = AsyncMock()
    judge.judge.return_value = 0.9

    repo = AsyncMock()
    repo.record.side_effect = RuntimeError("database exploded")

    sampler = _make_sampler(
        router=router, judge=judge, repo=repo, random_fn=lambda: 0.0
    )

    # Must not raise.
    await sampler.sample_if_applicable(
        skill=_make_skill(SkillState.SHADOW_PRIMARY),
        **_COMMON_KWARGS,
    )
