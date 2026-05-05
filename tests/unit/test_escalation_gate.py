"""Unit tests for EscalationGate."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
)
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tracker import CostSummary, CostTracker

_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT,
    parent_escalation_id INTEGER REFERENCES escalation_request(id)
);
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    output TEXT,
    quality_score REAL,
    is_shadow INTEGER DEFAULT 0,
    eval_session_id TEXT,
    spot_check_queued INTEGER DEFAULT 0,
    user_id TEXT NOT NULL,
    skill_id TEXT,
    escalation_request_id INTEGER
);
"""


def _config(enabled: bool = True, threshold: float = 5.0) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=enabled,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ManualEscalationModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=threshold,
        ),
    )


def _tracker(daily_total: float) -> CostTracker:
    t = MagicMock(spec=CostTracker)
    t.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_total, call_count=0, breakdown={})
    )
    return t


async def _await_correlation_id(
    repo: EscalationRepository, *, attempts: int = 100
) -> str:
    """Yield until the gate's row is in the DB, then return its correlation_id."""
    for _ in range(attempts):
        cursor = await repo._conn.execute(
            "SELECT correlation_id FROM escalation_request LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is not None:
            return row[0]
        await asyncio.sleep(0.01)
    raise AssertionError("gate did not create a row in time")


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "gate.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


def _gate(
    *,
    repo: EscalationRepository,
    daily_pause: float = 20.0,
    daily_total: float = 0.0,
    config: ManualEscalationConfig | None = None,
    deliver_returns: bool = True,
) -> tuple[EscalationGate, AsyncMock]:
    deliver = AsyncMock(return_value=deliver_returns)
    gate = EscalationGate(
        repository=repo,
        tracker=_tracker(daily_total),
        config=config or _config(),
        daily_pause_threshold_usd=daily_pause,
        resolver=DashboardSettingResolver(repo),
        deliver=deliver,
    )
    return gate, deliver


class TestShouldFire:
    async def test_does_not_fire_when_kill_switch_off(
        self, repo: EscalationRepository
    ) -> None:
        gate, deliver = _gate(repo=repo, config=_config(enabled=False))
        outcome = await gate.fire_and_wait(
            user_id="nick",
            task_id="t1",
            task_type="x",
            estimate_usd=999.0,
        )
        assert outcome.fired is False
        deliver.assert_not_called()

    async def test_does_not_fire_when_under_both_caps(
        self, repo: EscalationRepository
    ) -> None:
        # remaining=15 (20-5), threshold=5. estimate=2 → not over either.
        gate, deliver = _gate(repo=repo, daily_pause=20.0, daily_total=5.0)
        outcome = await gate.fire_and_wait(
            user_id="nick",
            task_id="t1",
            task_type="x",
            estimate_usd=2.0,
        )
        assert outcome.fired is False
        deliver.assert_not_called()

    async def test_dashboard_kill_switch_overrides_yaml(
        self, repo: EscalationRepository
    ) -> None:
        # YAML says enabled, dashboard says disabled.
        await repo.upsert_dashboard_setting("manual_escalation.enabled", False)
        gate, deliver = _gate(repo=repo)
        outcome = await gate.fire_and_wait(
            user_id="nick",
            task_id="t1",
            task_type="x",
            estimate_usd=1000.0,
        )
        assert outcome.fired is False
        deliver.assert_not_called()


class TestFireAndWait:
    async def test_fires_when_estimate_exceeds_threshold(
        self, repo: EscalationRepository
    ) -> None:
        gate, deliver = _gate(repo=repo, daily_pause=20.0, daily_total=0.0)
        # remaining=20, threshold=5. estimate=8 > min(20, 5) = 5 → fires.
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="x",
                estimate_usd=8.0,
            )
        )
        # Yield until the gate task has created its row and is awaiting
        # resolution; then resolve it from the test side.
        cid = await _await_correlation_id(repo)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id="t1",
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)
        assert outcome.fired is True
        assert outcome.mode == "pause"
        assert outcome.resolved_by == "user"
        deliver.assert_called_once()

    async def test_fires_when_remaining_below_estimate(
        self, repo: EscalationRepository
    ) -> None:
        gate, _deliver = _gate(repo=repo, daily_pause=20.0, daily_total=18.0)
        # remaining=2, threshold=5. estimate=3 > min(2,5)=2 → fires.
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="x",
                estimate_usd=3.0,
            )
        )
        cid = await _await_correlation_id(repo)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="cancel",
            owner_user_id="nick",
            task_id="t1",
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)
        assert outcome.mode == "cancel"

    async def test_offered_modes_are_pause_cancel(
        self, repo: EscalationRepository
    ) -> None:
        gate, _deliver = _gate(repo=repo, daily_total=0.0)
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=None,
                task_type="x",
                estimate_usd=10.0,
            )
        )
        cid = await _await_correlation_id(repo)
        cursor = await repo._conn.execute("SELECT offered_modes FROM escalation_request")
        (modes_raw,) = await cursor.fetchone()
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id=None,
        )
        await asyncio.wait_for(task, timeout=2.0)
        import json
        assert json.loads(modes_raw) == ["pause", "cancel"]


class TestRecordUserResolution:
    async def test_idempotent(self, repo: EscalationRepository) -> None:
        gate, _deliver = _gate(repo=repo, daily_total=0.0)
        # Direct row insert via repo.create — no fire_and_wait awaited.
        row = await repo.create(
            user_id="nick",
            correlation_id="iddir",
            task_id="t1",
            task_type="x",
            estimate_usd=10.0,
            daily_remaining_usd=0.0,
            offered_modes=["pause", "cancel"],
            priority=2,
        )
        first = await gate.record_user_resolution(
            correlation_id="iddir",
            mode="pause",
            owner_user_id="nick",
            task_id="t1",
        )
        second = await gate.record_user_resolution(
            correlation_id="iddir",
            mode="cancel",
            owner_user_id="nick",
            task_id="t1",
        )
        assert first is True
        assert second is False
        final = await repo.get(row.id)
        assert final is not None
        assert final.resolution == "pause"

    async def test_unknown_correlation_returns_false(
        self, repo: EscalationRepository
    ) -> None:
        gate, _deliver = _gate(repo=repo, daily_total=0.0)
        result = await gate.record_user_resolution(
            correlation_id="missing",
            mode="pause",
            owner_user_id="nick",
            task_id=None,
        )
        assert result is False
