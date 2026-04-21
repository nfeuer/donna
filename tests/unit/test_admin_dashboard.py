"""Unit tests for the admin dashboard KPI endpoints.

All tests mock the DB connection — no real SQLite needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.api.routes.admin_dashboard import (
    get_agent_performance,
    get_cost_analytics,
    get_parse_accuracy,
    get_quality_warnings,
    get_skill_system,
    get_task_throughput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone or (0,))
    return c


# ---------------------------------------------------------------------------
# ParseAccuracy
# ---------------------------------------------------------------------------


class TestParseAccuracy:
    async def test_empty_db_returns_100_accuracy(self, mock_request: tuple) -> None:
        request, conn = mock_request
        # 4 queries: daily_parses, daily_corrections, total_parses, total_corrections, field_breakdown
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),  # daily parses
                _cursor(),  # daily corrections
                _cursor(fetchone=(0,)),  # total parses
                _cursor(fetchone=(0,)),  # total corrections
                _cursor(),  # field breakdown
            ]
        )
        result = await get_parse_accuracy(request, days=30)
        assert result["summary"]["accuracy_pct"] == 100.0
        assert result["summary"]["total_parses"] == 0
        assert result["time_series"] == []

    async def test_single_day_with_corrections(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("2026-04-01", 10)]),  # daily parses
                _cursor(fetchall=[("2026-04-01", 2)]),   # daily corrections
                _cursor(fetchone=(10,)),  # total parses
                _cursor(fetchone=(2,)),   # total corrections
                _cursor(fetchall=[("title", 2)]),  # field breakdown
            ]
        )
        result = await get_parse_accuracy(request, days=30)
        assert result["summary"]["accuracy_pct"] == 80.0
        assert result["summary"]["most_corrected_field"] == "title"
        assert len(result["time_series"]) == 1
        assert result["time_series"][0]["accuracy"] == 80.0

    async def test_multi_day_time_series(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("2026-04-01", 5), ("2026-04-02", 10)]),
                _cursor(fetchall=[("2026-04-01", 1)]),  # only day 1 has corrections
                _cursor(fetchone=(15,)),
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[("domain", 1)]),
            ]
        )
        result = await get_parse_accuracy(request, days=30)
        ts = result["time_series"]
        assert len(ts) == 2
        # Day 1: 5 parses, 1 correction → 80%
        assert ts[0]["accuracy"] == 80.0
        # Day 2: 10 parses, 0 corrections → 100%
        assert ts[1]["accuracy"] == 100.0

    async def test_field_breakdown_ordering(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),
                _cursor(),
                _cursor(fetchone=(20,)),
                _cursor(fetchone=(5,)),
                _cursor(fetchall=[("priority", 3), ("domain", 2)]),
            ]
        )
        result = await get_parse_accuracy(request, days=7)
        fb = result["field_breakdown"]
        assert fb[0]["field"] == "priority"
        assert fb[0]["count"] == 3


# ---------------------------------------------------------------------------
# AgentPerformance
# ---------------------------------------------------------------------------


class TestAgentPerformance:
    async def test_no_invocations(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),  # per-task-type agg
                _cursor(),  # daily series
                _cursor(),  # all latencies
            ]
        )
        result = await get_agent_performance(request, days=30)
        assert result["summary"]["total_calls"] == 0
        assert result["agents"] == []

    async def test_single_task_type(self, mock_request: tuple) -> None:
        request, conn = mock_request
        # task_type, count, avg_lat, max_lat, tok_in, tok_out, cost, avg_cost, scored, avg_q
        agent_row = ("parse_task", 10, 500.0, 1200, 5000, 2000, 0.05, 0.005, 8, 0.85)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[agent_row]),
                _cursor(),  # daily series
                _cursor(fetchall=[(i * 100,) for i in range(10)]),  # latencies
            ]
        )
        result = await get_agent_performance(request, days=30)
        assert result["summary"]["total_calls"] == 10
        assert len(result["agents"]) == 1
        assert result["agents"][0]["task_type"] == "parse_task"

    async def test_p95_latency_calculation(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("parse_task", 20, 100.0, 500, 1000, 500, 0.01, 0.0005, 0, None)]),
                _cursor(),
                # 20 sorted latencies: 10, 20, ..., 200
                _cursor(fetchall=[(i * 10,) for i in range(1, 21)]),
            ]
        )
        result = await get_agent_performance(request, days=30)
        # p95 of [10..200] → index int(20*0.95)=19 → 200
        assert result["summary"]["p95_latency_ms"] == 200


# ---------------------------------------------------------------------------
# TaskThroughput
# ---------------------------------------------------------------------------


class TestTaskThroughput:
    async def test_no_tasks(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),  # daily created
                _cursor(),  # daily completed
                _cursor(),  # status dist
                _cursor(fetchone=(0,)),  # total created
                _cursor(fetchone=(0,)),  # total completed
                _cursor(fetchone=(0,)),  # overdue
                _cursor(fetchone=(None,)),  # avg reschedules
                _cursor(fetchone=(None,)),  # avg completion hours
                _cursor(),  # domain breakdown
            ]
        )
        result = await get_task_throughput(request, days=30)
        assert result["summary"]["total_created"] == 0
        assert result["summary"]["completion_rate"] == 0

    async def test_tasks_with_completions(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("2026-04-01", 5)]),
                _cursor(fetchall=[("2026-04-01", 3)]),
                _cursor(fetchall=[("done", 3), ("scheduled", 2)]),
                _cursor(fetchone=(5,)),
                _cursor(fetchone=(3,)),
                _cursor(fetchone=(1,)),   # 1 overdue
                _cursor(fetchone=(2.5,)),  # avg reschedules
                _cursor(fetchone=(48.0,)),  # avg completion hours
                _cursor(fetchall=[("work", 3, 2), ("personal", 2, 1)]),
            ]
        )
        result = await get_task_throughput(request, days=30)
        assert result["summary"]["total_created"] == 5
        assert result["summary"]["completion_rate"] == 60.0
        assert result["summary"]["overdue_count"] == 1
        assert result["summary"]["avg_reschedules"] == 2.5
        assert result["status_distribution"]["done"] == 3


# ---------------------------------------------------------------------------
# CostAnalytics
# ---------------------------------------------------------------------------


class TestCostAnalytics:
    async def test_zero_cost(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),  # daily time series
                _cursor(fetchone=(0, 0)),  # today
                _cursor(fetchone=(0, 0)),  # monthly
                _cursor(fetchone=(0,)),  # 7-day
                _cursor(),  # by task_type
                _cursor(),  # by model
            ]
        )
        result = await get_cost_analytics(request, days=30)
        assert result["summary"]["today_cost_usd"] == 0.0
        assert result["summary"]["daily_utilization_pct"] == 0.0

    async def test_budget_utilization_math(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("2026-04-06", 5.0, 50)]),
                _cursor(fetchone=(5.0, 50)),  # today: $5
                _cursor(fetchone=(40.0, 400)),  # month: $40
                _cursor(fetchone=(35.0,)),  # 7-day: $35
                _cursor(fetchall=[("parse_task", 30.0, 300)]),
                _cursor(fetchall=[("claude-sonnet", 30.0, 300)]),
            ]
        )
        result = await get_cost_analytics(request, days=30)
        assert result["summary"]["today_cost_usd"] == 5.0
        assert result["summary"]["daily_utilization_pct"] == 25.0  # 5/20 * 100
        assert result["summary"]["monthly_utilization_pct"] == 40.0  # 40/100 * 100
        assert result["summary"]["daily_remaining_usd"] == 15.0
        assert result["summary"]["monthly_remaining_usd"] == 60.0


# ---------------------------------------------------------------------------
# ParseAccuracy — clamp test
# ---------------------------------------------------------------------------


class TestParseAccuracyClamp:
    async def test_accuracy_clamped_when_corrections_exceed_parses(
        self, mock_request: tuple
    ) -> None:
        """When corrections > parses, accuracy should clamp to 0, not go negative."""
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("2026-04-01", 3)]),   # daily parses
                _cursor(fetchall=[("2026-04-01", 10)]),  # daily corrections (more than parses)
                _cursor(fetchone=(3,)),   # total parses
                _cursor(fetchone=(10,)),  # total corrections
                _cursor(fetchall=[("title", 10)]),  # field breakdown
            ]
        )
        result = await get_parse_accuracy(request, days=30)
        # Should clamp to 0, not go to -233%
        assert result["summary"]["accuracy_pct"] == 0.0
        assert result["time_series"][0]["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# QualityWarnings
# ---------------------------------------------------------------------------


class TestQualityWarnings:
    async def test_no_scored_invocations(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),  # daily time series
                _cursor(fetchone=(0, 0, 0)),  # totals
                _cursor(),  # by task_type
            ]
        )
        result = await get_quality_warnings(request, days=30)
        assert result["summary"]["total_warnings"] == 0
        assert result["summary"]["total_criticals"] == 0
        assert result["summary"]["warning_rate_pct"] == 0.0
        assert "thresholds" in result

    async def test_warnings_and_criticals(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("2026-04-01", 3, 1)]),  # daily: 3 warn, 1 crit
                _cursor(fetchone=(3, 1, 50)),  # totals: 3 warn, 1 crit, 50 scored
                _cursor(fetchall=[("parse_task", 2, 1, 30)]),  # by task_type
            ]
        )
        result = await get_quality_warnings(request, days=30)
        assert result["summary"]["total_warnings"] == 3
        assert result["summary"]["total_criticals"] == 1
        assert result["summary"]["warning_rate_pct"] == 8.0  # (3+1)/50*100
        assert len(result["by_task_type"]) == 1
        assert result["by_task_type"][0]["task_type"] == "parse_task"

    async def test_thresholds_returned(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),
                _cursor(fetchone=(0, 0, 0)),
                _cursor(),
            ]
        )
        result = await get_quality_warnings(request, days=7)
        assert result["thresholds"]["warning_threshold"] == 0.65
        assert result["thresholds"]["critical_threshold"] == 0.3


# ---------------------------------------------------------------------------
# SkillSystem aggregator (Wave 4 F-4)
# ---------------------------------------------------------------------------


class TestSkillSystem:
    async def test_empty_db_returns_zero_everything(self, mock_request: tuple) -> None:
        request, conn = mock_request
        # 7 queries in order: by_state, new_candidates, evolution, active_automations,
        # automation_runs, automation_failures, run_time_series
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),                       # by_state
                _cursor(fetchone=(0,)),          # new candidates
                _cursor(),                       # evolution outcomes
                _cursor(fetchone=(0,)),          # active automations
                _cursor(fetchone=(0,)),          # automation runs
                _cursor(fetchone=(0,)),          # automation failures
                _cursor(),                       # run time series
            ]
        )
        result = await get_skill_system(request, days=30)
        assert result["summary"]["total_skills"] == 0
        assert result["summary"]["new_candidates_24h"] == 0
        assert result["summary"]["evolution_success_rate_24h"] is None
        assert result["summary"]["active_automations"] == 0
        assert result["summary"]["automation_failure_rate_pct"] == 0.0
        # all state buckets initialized to 0
        for state in (
            "claude_native",
            "skill_candidate",
            "draft",
            "sandbox",
            "shadow_primary",
            "trusted",
            "flagged_for_review",
            "degraded",
        ):
            assert result["by_state"][state] == 0
        assert result["anomalies"] == []

    async def test_state_counts_and_totals(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("trusted", 3), ("draft", 2), ("degraded", 1)]),
                _cursor(fetchone=(2,)),           # new candidates 24h
                _cursor(fetchall=[("success", 4), ("failed", 1)]),
                _cursor(fetchone=(5,)),           # active automations
                _cursor(fetchone=(10,)),          # automation runs 24h
                _cursor(fetchone=(2,)),           # automation failures 24h
                _cursor(fetchall=[("2026-04-20", 7), ("2026-04-21", 3)]),
            ]
        )
        result = await get_skill_system(request, days=30)
        assert result["summary"]["total_skills"] == 6
        assert result["by_state"]["trusted"] == 3
        assert result["by_state"]["draft"] == 2
        assert result["by_state"]["degraded"] == 1
        assert result["summary"]["new_candidates_24h"] == 2
        assert result["summary"]["evolution_success_rate_24h"] == 80.0
        assert result["summary"]["active_automations"] == 5
        assert result["summary"]["automation_runs_24h"] == 10
        assert result["summary"]["automation_failures_24h"] == 2
        assert result["summary"]["automation_failure_rate_pct"] == 20.0
        assert len(result["run_time_series"]) == 2

    async def test_anomaly_degraded_skill(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[("degraded", 2)]),
                _cursor(fetchone=(0,)),
                _cursor(),
                _cursor(fetchone=(0,)),
                _cursor(fetchone=(0,)),
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        result = await get_skill_system(request, days=30)
        kinds = [a["kind"] for a in result["anomalies"]]
        assert "degraded_skill" in kinds

    async def test_anomaly_automation_failure_rate(self, mock_request: tuple) -> None:
        request, conn = mock_request
        # 20% failure rate > default 10% threshold
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),
                _cursor(fetchone=(0,)),
                _cursor(),
                _cursor(fetchone=(1,)),
                _cursor(fetchone=(10,)),
                _cursor(fetchone=(2,)),
                _cursor(),
            ]
        )
        result = await get_skill_system(request, days=30)
        kinds = [a["kind"] for a in result["anomalies"]]
        assert "automation_failure_rate" in kinds

    async def test_anomaly_new_candidates_spike(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),
                _cursor(fetchone=(9,)),           # above default 5
                _cursor(),
                _cursor(fetchone=(0,)),
                _cursor(fetchone=(0,)),
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        result = await get_skill_system(request, days=30)
        kinds = [a["kind"] for a in result["anomalies"]]
        assert "new_candidates" in kinds
