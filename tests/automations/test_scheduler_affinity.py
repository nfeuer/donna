"""Tests for scheduler model-affinity grouping."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.scheduler import AutomationScheduler


def _make_row(aid: str, gpu_model: str | None = None) -> MagicMock:
    row = MagicMock()
    row.id = aid
    row.gpu_model = gpu_model
    return row


@pytest.mark.asyncio
async def test_groups_by_gpu_model_home_first():
    """Due automations are grouped: home model first, then each non-home group."""
    repo = AsyncMock()
    dispatcher = AsyncMock()

    rows = [
        _make_row("a1", gpu_model="qwen2.5-vl:7b"),
        _make_row("a2", gpu_model=None),
        _make_row("a3", gpu_model="qwen2.5-vl:7b"),
        _make_row("a4", gpu_model=None),
    ]
    repo.list_due = AsyncMock(return_value=rows)

    sched = AutomationScheduler(
        repository=repo,
        dispatcher=dispatcher,
        poll_interval_seconds=60,
        gpu_home_model="qwen2.5:32b-instruct-q6_K",
    )
    await sched.run_once()

    dispatched_ids = [call.args[0].id for call in dispatcher.dispatch.call_args_list]
    assert dispatched_ids == ["a2", "a4", "a1", "a3"]


@pytest.mark.asyncio
async def test_no_gpu_model_dispatches_all():
    """When no rows have gpu_model, dispatch in original order."""
    repo = AsyncMock()
    dispatcher = AsyncMock()

    rows = [_make_row("a1"), _make_row("a2"), _make_row("a3")]
    repo.list_due = AsyncMock(return_value=rows)

    sched = AutomationScheduler(
        repository=repo,
        dispatcher=dispatcher,
        poll_interval_seconds=60,
    )
    await sched.run_once()

    dispatched_ids = [call.args[0].id for call in dispatcher.dispatch.call_args_list]
    assert dispatched_ids == ["a1", "a2", "a3"]


@pytest.mark.asyncio
async def test_no_home_model_skips_grouping():
    """When gpu_home_model is None, rows are dispatched as-is."""
    repo = AsyncMock()
    dispatcher = AsyncMock()

    rows = [
        _make_row("a1", gpu_model="qwen2.5-vl:7b"),
        _make_row("a2", gpu_model=None),
    ]
    repo.list_due = AsyncMock(return_value=rows)

    sched = AutomationScheduler(
        repository=repo,
        dispatcher=dispatcher,
        poll_interval_seconds=60,
        gpu_home_model=None,
    )
    await sched.run_once()

    dispatched_ids = [call.args[0].id for call in dispatcher.dispatch.call_args_list]
    assert dispatched_ids == ["a1", "a2"]
