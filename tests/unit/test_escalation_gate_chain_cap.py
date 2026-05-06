"""Slice 25 — :meth:`EscalationGate.fire_and_wait` chain-cap regression.

Pins the depth-cap path that token-cap recovery relies on. Spec
§10.6 row 1 + §12 Q5: a re-fire whose persisted depth would exceed
``triggers.max_re_escalation_depth`` must:

  1. NOT create a new ``escalation_request`` row.
  2. Write a single ``re_escalation_chain_capped`` audit event keyed
     off the parent's id.
  3. Return ``GateOutcome(mode='cancel', resolved_by='system')``.

Also pins:

  * The dedup-bypass invariant — a re-fire (parent_escalation_id set)
    skips the ``find_open_for_originating_entity`` check so chain
    links past the first don't short-circuit.
  * Slice-23 runtime override — the cap reads through
    :class:`DashboardSettingResolver` so a dashboard knob can adjust
    it mid-flight.

Tests that actually create a new row use the slice-17 pattern of
firing in a background task and resolving via
``gate.record_user_resolution`` so the awaited future returns; the
chain-cap path short-circuits before the await and does not need this.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    ClaudeCodeModeConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
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
    parent_escalation_id INTEGER REFERENCES escalation_request(id),
    human_review INTEGER NOT NULL DEFAULT 0,
    target_paths TEXT,
    originating_entity_type TEXT,
    originating_entity_id TEXT,
    base_sha TEXT,
    merged_at TEXT
);
CREATE INDEX ix_escalation_request_parent_escalation_id
    ON escalation_request(parent_escalation_id);
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
    is_shadow INTEGER DEFAULT 0,
    spot_check_queued INTEGER DEFAULT 0,
    user_id TEXT NOT NULL,
    escalation_request_id INTEGER
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "gate-chain.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> EscalationRepository:
    return EscalationRepository(conn)


def _config(*, max_depth: int = 5) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=False),
            claude_code=ClaudeCodeModeConfig(enabled=False),
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=1.0,
            escalation_timeout_minutes=60,
            manual_iteration_limit=3,
            max_re_escalation_depth=max_depth,
        ),
    )


def _tracker(daily_total: float = 0.0) -> CostTracker:
    t = MagicMock(spec=CostTracker)
    t.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_total, call_count=0, breakdown={})
    )
    return t


def _stub_ext_repo() -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=0.0)
    stub.get_monthly_total = AsyncMock(return_value=0.0)
    return stub


def _gate(
    *,
    repo: EscalationRepository,
    config: ManualEscalationConfig | None = None,
) -> tuple[EscalationGate, AsyncMock]:
    deliver = AsyncMock(return_value=True)
    gate = EscalationGate(
        repository=repo,
        tracker=_tracker(),
        config=config or _config(),
        daily_pause_threshold_usd=0.0,
        resolver=DashboardSettingResolver(repo),
        deliver=deliver,
        extension_repo=_stub_ext_repo(),
    )
    return gate, deliver


async def _seed_chain(
    repo: EscalationRepository,
    *,
    user_id: str,
    length: int,
    correlation_prefix: str,
) -> list[int]:
    """Build a chain of ``length`` rows. Returns ids in insertion order."""
    parent: int | None = None
    ids: list[int] = []
    for i in range(length):
        row = await repo.create(
            user_id=user_id,
            correlation_id=f"{correlation_prefix}-{i}",
            task_id=None,
            task_type="chat_escalation",
            estimate_usd=1.0,
            daily_remaining_usd=0.0,
            offered_modes=["api_extended", "pause", "cancel"],
            priority=2,
            parent_escalation_id=parent,
        )
        ids.append(row.id)
        parent = row.id
    return ids


async def _row_count(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) FROM escalation_request")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _audit_events(
    conn: aiosqlite.Connection, escalation_request_id: int
) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT output FROM invocation_log
         WHERE escalation_request_id = ?
           AND task_type = 'escalation_lifecycle'
         ORDER BY timestamp ASC
        """,
        (escalation_request_id,),
    )
    return [json.loads(r[0]) for r in await cursor.fetchall()]


class TestChainCap:
    async def test_cap_honoured_at_exact_depth(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """A re-fire whose new depth equals the cap is rejected."""
        # Seed a chain whose tip already sits at depth=2 (3 rows: 0,1,2).
        ids = await _seed_chain(
            repo, user_id="nick", length=3, correlation_prefix="cap-2"
        )
        gate, _deliver = _gate(repo=repo, config=_config(max_depth=2))

        before = await _row_count(conn)
        outcome = await gate.fire_and_wait(
            user_id="nick",
            task_id="t1",
            task_type="chat_escalation",
            estimate_usd=10.0,
            parent_escalation_id=ids[-1],
        )

        assert outcome.fired is True
        assert outcome.mode == "cancel"
        assert outcome.resolved_by == "system"
        assert outcome.escalation_request_id == ids[-1]
        # No new row created.
        assert await _row_count(conn) == before
        # Audit event keyed off the parent.
        events = await _audit_events(conn, ids[-1])
        assert any(e["event"] == "re_escalation_chain_capped" for e in events)

    async def test_cap_one_under_allows_fire(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """A re-fire one link under the cap creates the new row."""
        ids = await _seed_chain(
            repo, user_id="nick", length=2, correlation_prefix="under"
        )
        gate, _deliver = _gate(repo=repo, config=_config(max_depth=5))

        before = await _row_count(conn)
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=None,
                task_type="chat_escalation",
                estimate_usd=10.0,
                parent_escalation_id=ids[-1],
            )
        )
        # The new row's correlation id is the most recent one inserted.
        new_correlation = await _wait_for_new_correlation(conn, before)
        await gate.record_user_resolution(
            correlation_id=new_correlation,
            mode="pause",
            owner_user_id="nick",
            task_id=None,
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)

        assert outcome.fired is True
        assert await _row_count(conn) == before + 1
        # New row has parent_escalation_id set.
        cursor = await conn.execute(
            "SELECT parent_escalation_id FROM escalation_request "
            "WHERE correlation_id = ?",
            (new_correlation,),
        )
        row = await cursor.fetchone()
        assert row is not None and int(row[0]) == ids[-1]

    async def test_cap_not_enforced_without_parent(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """A fresh fire (no parent) is unaffected by the cap."""
        gate, _deliver = _gate(repo=repo, config=_config(max_depth=1))
        before = await _row_count(conn)
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=None,
                task_type="chat_escalation",
                estimate_usd=10.0,
            )
        )
        new_correlation = await _wait_for_new_correlation(conn, before)
        await gate.record_user_resolution(
            correlation_id=new_correlation,
            mode="pause",
            owner_user_id="nick",
            task_id=None,
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)
        assert outcome.fired is True
        assert outcome.mode != "cancel" or outcome.resolved_by != "system"

    async def test_runtime_cap_override(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """Slice-23 dashboard override: cap can be tightened mid-flight."""
        ids = await _seed_chain(
            repo, user_id="nick", length=2, correlation_prefix="rt"
        )
        # YAML default 5; dashboard override forces it to 1 so the
        # next re-fire (depth=2) is over cap.
        await repo.upsert_dashboard_setting(
            "manual_escalation.triggers.max_re_escalation_depth", 1
        )
        gate, _deliver = _gate(repo=repo, config=_config(max_depth=5))

        outcome = await gate.fire_and_wait(
            user_id="nick",
            task_id=None,
            task_type="chat_escalation",
            estimate_usd=10.0,
            parent_escalation_id=ids[-1],
        )
        assert outcome.mode == "cancel"
        assert outcome.resolved_by == "system"

    async def test_dedup_guard_bypassed_for_re_fire(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """A re-fire targeting the same originating entity as the parent
        does NOT short-circuit through ``find_open_for_originating_entity``.
        Without the bypass, every chain past the first link would
        terminate.
        """
        # Seed a row whose originating_entity matches the re-fire's.
        first = await repo.create(
            user_id="nick",
            correlation_id="dedup-parent",
            task_id=None,
            task_type="skill_auto_draft",
            estimate_usd=5.0,
            daily_remaining_usd=0.0,
            offered_modes=["api_extended", "pause"],
            priority=2,
            originating_entity=("skill_candidate_report", "cand-99"),
        )
        gate, _deliver = _gate(repo=repo, config=_config(max_depth=5))

        before = await _row_count(conn)
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=None,
                task_type="skill_auto_draft",
                estimate_usd=8.0,
                originating_entity=("skill_candidate_report", "cand-99"),
                parent_escalation_id=first.id,
            )
        )
        new_correlation = await _wait_for_new_correlation(conn, before)
        await gate.record_user_resolution(
            correlation_id=new_correlation,
            mode="pause",
            owner_user_id="nick",
            task_id=None,
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)
        # Re-fire created a new row despite the matching originating
        # entity — proves the dedup bypass works.
        assert await _row_count(conn) == before + 1
        assert outcome.fired is True

    async def test_invalid_runtime_cap_falls_back_to_yaml(
        self,
        repo: EscalationRepository,
        conn: aiosqlite.Connection,
    ) -> None:
        """A non-integer / negative runtime override falls back to the YAML
        default rather than crashing the gate."""
        ids = await _seed_chain(
            repo, user_id="nick", length=4, correlation_prefix="bad"
        )
        await repo.upsert_dashboard_setting(
            "manual_escalation.triggers.max_re_escalation_depth", "garbage"
        )
        gate, _deliver = _gate(repo=repo, config=_config(max_depth=5))

        before = await _row_count(conn)
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=None,
                task_type="chat_escalation",
                estimate_usd=10.0,
                parent_escalation_id=ids[-1],
            )
        )
        new_correlation = await _wait_for_new_correlation(conn, before)
        await gate.record_user_resolution(
            correlation_id=new_correlation,
            mode="pause",
            owner_user_id="nick",
            task_id=None,
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)
        # YAML default 5, chain depth 4 → fire allowed (new row created).
        assert outcome.fired is True
        # `cancel` only if the cap rejected; we expect no chain-cap here.
        assert not (
            outcome.mode == "cancel" and outcome.resolved_by == "system"
        )


async def _wait_for_new_correlation(
    conn: aiosqlite.Connection, baseline: int, *, attempts: int = 100
) -> str:
    """Yield until a new escalation_request row has landed, then return its
    correlation_id. Mirrors ``_await_correlation_id`` in the slice-17
    tests but keyed off a row-count baseline so chains seeded with
    multiple existing rows still surface only the newest one.
    """
    for _ in range(attempts):
        cursor = await conn.execute(
            "SELECT correlation_id FROM escalation_request "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        cnt_cursor = await conn.execute("SELECT COUNT(*) FROM escalation_request")
        cnt_row = await cnt_cursor.fetchone()
        cnt = int(cnt_row[0]) if cnt_row else 0
        if cnt > baseline and row is not None:
            return str(row[0])
        await asyncio.sleep(0.01)
    raise AssertionError("gate did not create a new row in time")
