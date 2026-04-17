"""Tests for EquivalenceJudge — Claude-backed semantic equivalence judge."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.skills.equivalence import EquivalenceJudge


async def test_judge_returns_agreement_from_router():
    router = AsyncMock()
    router.complete.return_value = ({"agreement": 0.9, "rationale": "same"}, object())
    judge = EquivalenceJudge(router)

    result = await judge.judge({"x": 1}, {"x": 1}, context={"capability": "parse_task"})

    assert result == pytest.approx(0.9)
    assert router.complete.await_count == 1
    call_kwargs = router.complete.await_args.kwargs
    assert call_kwargs["task_type"] == "skill_equivalence_judge"


async def test_judge_returns_zero_on_router_exception():
    router = AsyncMock()
    router.complete.side_effect = RuntimeError("boom")
    judge = EquivalenceJudge(router)

    result = await judge.judge({"a": 1}, {"b": 2})
    assert result == 0.0


async def test_judge_returns_zero_on_missing_agreement_key():
    router = AsyncMock()
    router.complete.return_value = ({}, object())
    judge = EquivalenceJudge(router)

    result = await judge.judge({"a": 1}, {"b": 2})
    assert result == 0.0


async def test_judge_returns_zero_on_non_numeric_agreement():
    router = AsyncMock()
    router.complete.return_value = ({"agreement": "high", "rationale": "looks good"}, object())
    judge = EquivalenceJudge(router)

    result = await judge.judge({"a": 1}, {"b": 2})
    assert result == 0.0


async def test_judge_clamps_out_of_range():
    router_high = AsyncMock()
    router_high.complete.return_value = ({"agreement": 1.5, "rationale": "x"}, object())
    judge_high = EquivalenceJudge(router_high)

    result_high = await judge_high.judge({"a": 1}, {"b": 2})
    assert result_high == pytest.approx(1.0)

    router_low = AsyncMock()
    router_low.complete.return_value = ({"agreement": -0.3, "rationale": "y"}, object())
    judge_low = EquivalenceJudge(router_low)

    result_low = await judge_low.judge({"a": 1}, {"b": 2})
    assert result_low == pytest.approx(0.0)


async def test_judge_includes_context_in_prompt():
    router = AsyncMock()
    router.complete.return_value = ({"agreement": 0.5, "rationale": "partial"}, object())
    judge = EquivalenceJudge(router)
    ctx = {"capability": "parse_task", "description": "extract structured fields"}

    await judge.judge({"x": 1}, {"x": 2}, context=ctx)

    call_kwargs = router.complete.await_args.kwargs
    prompt: str = call_kwargs["prompt"]
    assert "parse_task" in prompt
    assert "extract structured fields" in prompt


async def test_judge_uses_configured_task_type():
    router = AsyncMock()
    router.complete.return_value = ({"agreement": 0.8, "rationale": "ok"}, object())
    judge = EquivalenceJudge(router, task_type="custom_equivalence_judge")

    await judge.judge({"a": 1}, {"a": 1})

    call_kwargs = router.complete.await_args.kwargs
    assert call_kwargs["task_type"] == "custom_equivalence_judge"
