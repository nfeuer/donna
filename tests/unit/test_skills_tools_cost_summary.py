"""Tests for cost_summary skill-system tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.cost.tracker import CostSummary
from donna.skills.tools.cost_summary import cost_summary, CostSummaryError


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.get_daily_cost = AsyncMock()
    c.get_monthly_cost = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_cost_summary_daily_returns_totals(fake_client):
    fake_client.get_daily_cost.return_value = CostSummary(
        total_usd=4.2, call_count=17, breakdown={"task_parse": 1.5, "digest": 2.7},
    )
    out = await cost_summary(client=fake_client, scope="daily", for_date="2026-04-21")
    assert out["ok"] is True
    assert out["scope"] == "daily"
    assert out["total_usd"] == pytest.approx(4.2)
    assert out["call_count"] == 17
    assert out["breakdown"] == {"task_parse": 1.5, "digest": 2.7}


@pytest.mark.asyncio
async def test_cost_summary_daily_without_date_uses_today(fake_client):
    fake_client.get_daily_cost.return_value = CostSummary(0.0, 0, {})
    await cost_summary(client=fake_client, scope="daily")
    assert fake_client.get_daily_cost.call_args.kwargs["for_date"] is None


@pytest.mark.asyncio
async def test_cost_summary_daily_passes_exclude_filter(fake_client):
    fake_client.get_daily_cost.return_value = CostSummary(0.0, 0, {})
    await cost_summary(
        client=fake_client, scope="daily",
        exclude_task_types=["skill_auto_draft"],
    )
    assert fake_client.get_daily_cost.call_args.kwargs["exclude_task_types"] == [
        "skill_auto_draft"
    ]


@pytest.mark.asyncio
async def test_cost_summary_monthly(fake_client):
    fake_client.get_monthly_cost.return_value = CostSummary(
        total_usd=88.0, call_count=400, breakdown={"digest": 88.0},
    )
    out = await cost_summary(
        client=fake_client, scope="monthly", year=2026, month=4,
    )
    assert out["ok"] is True
    assert out["scope"] == "monthly"
    assert out["total_usd"] == pytest.approx(88.0)
    assert fake_client.get_monthly_cost.call_args.kwargs == {"year": 2026, "month": 4}


@pytest.mark.asyncio
async def test_cost_summary_monthly_rejects_exclude_filter(fake_client):
    with pytest.raises(CostSummaryError):
        await cost_summary(
            client=fake_client, scope="monthly",
            exclude_task_types=["x"],
        )


@pytest.mark.asyncio
async def test_cost_summary_bad_date_raises(fake_client):
    with pytest.raises(CostSummaryError):
        await cost_summary(client=fake_client, scope="daily", for_date="21-04-2026")


@pytest.mark.asyncio
async def test_cost_summary_unknown_scope_raises(fake_client):
    with pytest.raises(CostSummaryError):
        await cost_summary(client=fake_client, scope="weekly")


@pytest.mark.asyncio
async def test_cost_summary_propagates_client_failure(fake_client):
    fake_client.get_daily_cost.side_effect = RuntimeError("db locked")
    with pytest.raises(CostSummaryError):
        await cost_summary(client=fake_client, scope="daily")
