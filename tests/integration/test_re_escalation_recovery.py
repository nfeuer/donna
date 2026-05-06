"""Slice 25 — token-cap recovery integration test.

Drives the full ``ModelRouter.complete()`` → ``TokenLimitReachedError``
→ :class:`ReEscalationCoordinator` → :meth:`EscalationGate.fire_and_wait`
loop with a real SQLite DB and a fake provider that flips between
truncated and non-truncated responses. Mirrors the dashboard timeline
contract: every link in the chain emits the
``re_escalation_offered`` audit event before its ``escalation_offered``
row, and the chain-cap path emits ``re_escalation_token_limited`` on
the parent.

Realises docs/superpowers/specs/manual-escalation.md §10.6 row 1
(re-estimate + re-escalation), §11 functional acceptance.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    BudgetExtensionConfig,
    ClaudeCodeModeConfig,
    CostConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
    QualityMonitoringConfig,
    RoutingEntry,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.re_escalation_coordinator import ReEscalationCoordinator
from donna.cost.tracker import CostSummary, CostTracker
from donna.models.router import ModelRouter, TokenLimitReachedError
from donna.models.types import CompletionMetadata

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
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
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
CREATE TABLE daily_budget_extension (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    escalation_request_id INTEGER REFERENCES escalation_request(id),
    voided INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_daily_budget_extension_idempotency
    ON daily_budget_extension (escalation_request_id, granted_by);
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
    c = await aiosqlite.connect(str(tmp_path / "recovery.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


def _models_config() -> ModelsConfig:
    return ModelsConfig(
        models={
            "parser": ModelConfig(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                input_cost_per_token_usd=0.000003,
                output_cost_per_token_usd=0.000015,
            )
        },
        routing={"skill_draft": RoutingEntry(model="parser")},
        cost=CostConfig(daily_pause_threshold_usd=20.0, monthly_budget_usd=100.0),
        ollama=OllamaConfig(),
        quality_monitoring=QualityMonitoringConfig(),
    )


def _task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "skill_draft": TaskTypeEntry(
                description="test",
                model="parser",
                prompt_template="prompts/skill_draft.md",
                output_schema="schemas/skill_draft.json",
            )
        }
    )


def _manual_config(*, max_depth: int = 5) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=False),
            claude_code=ClaudeCodeModeConfig(enabled=False),
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=1.0,
            max_re_escalation_depth=max_depth,
            re_escalation_estimate_multiplier=2.0,
        ),
        budget_extension=BudgetExtensionConfig(
            enabled=True,
            max_daily_extension_usd=50.0,
            hard_monthly_ceiling_usd=200.0,
        ),
    )


def _tracker() -> CostTracker:
    t = MagicMock(spec=CostTracker)
    t.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
    )
    return t


def _meta(token_limited: bool) -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=10,
        tokens_in=50,
        tokens_out=20,
        cost_usd=0.001,
        model_actual="anthropic/claude-sonnet-4-20250514",
        token_limited=token_limited,
    )


async def _build_router(
    *,
    conn: aiosqlite.Connection,
    provider_responses: list[tuple[dict, CompletionMetadata]],
    max_depth: int = 5,
) -> tuple[ModelRouter, EscalationRepository]:
    """Wire router + gate + coordinator with a fake provider returning
    the given responses in order. Each ``router.complete`` call (or
    recursion) consumes one response."""
    repo = EscalationRepository(conn)
    extension_repo = BudgetExtensionRepository(conn)
    config = _manual_config(max_depth=max_depth)
    deliver = AsyncMock(return_value=True)

    gate = EscalationGate(
        repository=repo,
        tracker=_tracker(),
        config=config,
        daily_pause_threshold_usd=20.0,
        resolver=DashboardSettingResolver(repo),
        deliver=deliver,
        extension_repo=extension_repo,
    )
    coordinator = ReEscalationCoordinator(
        gate=gate,
        repo=repo,
        extension_repo=extension_repo,
        manual_escalation_config=config,
    )

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=provider_responses)

    router = ModelRouter(
        models_config=_models_config(),
        task_types_config=_task_types_config(),
        project_root=Path("/nonexistent"),
        escalation_gate=gate,
        re_escalation_coordinator=coordinator,
    )
    router._providers["anthropic"] = mock_provider
    return router, repo


async def _wait_for_open_correlation(
    repo: EscalationRepository, *, attempts: int = 100
) -> str:
    """Yield until the latest open row exists; return its correlation."""
    for _ in range(attempts):
        cursor = await repo._conn.execute(
            "SELECT correlation_id FROM escalation_request "
            "WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is not None:
            return str(row[0])
        await asyncio.sleep(0.01)
    raise AssertionError("no open escalation row appeared in time")


async def _audit_events_for(
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


class TestRecoveryHappyPath:
    async def test_post_call_truncation_recovers(
        self,
        conn: aiosqlite.Connection,
    ) -> None:
        """First completion is truncated → coordinator re-fires →
        second completion succeeds. Final result is the second one."""
        router, repo = await _build_router(
            conn=conn,
            provider_responses=[
                ({"result": "truncated"}, _meta(token_limited=True)),
                ({"result": "complete"}, _meta(token_limited=False)),
            ],
        )

        # Drive the call in a task so we can resolve gate prompts.
        complete_task = asyncio.create_task(
            router.complete(
                prompt="do something expensive",
                task_type="skill_draft",
                task_id="task-1",
                user_id="nick",
                estimate_usd=5.0,
            )
        )

        # First gate fire → user grants extension.
        cid_first = await _wait_for_open_correlation(repo)
        await router._escalation_gate.record_user_resolution(
            correlation_id=cid_first,
            mode="api_extended",
            owner_user_id="nick",
            task_id="task-1",
        )

        # The recovery re-fires the gate → second open row → user grants.
        cid_second = await _wait_for_open_correlation(repo)
        # If the same correlation_id is returned, the coordinator hasn't
        # fired yet; back off briefly.
        for _ in range(50):
            if cid_second != cid_first:
                break
            await asyncio.sleep(0.02)
            cid_second = await _wait_for_open_correlation(repo)
        assert cid_second != cid_first
        await router._escalation_gate.record_user_resolution(
            correlation_id=cid_second,
            mode="api_extended",
            owner_user_id="nick",
            task_id="task-1",
        )

        result, _meta_final = await asyncio.wait_for(complete_task, timeout=5.0)
        assert result == {"result": "complete"}

        # Two rows landed; second carries parent_escalation_id of first.
        cursor = await conn.execute(
            "SELECT id, correlation_id, parent_escalation_id "
            "FROM escalation_request ORDER BY id ASC"
        )
        rows = list(await cursor.fetchall())
        assert len(rows) == 2
        first_id = rows[0][0]
        second_parent = rows[1][2]
        assert second_parent == first_id

        # `re_escalation_offered` event landed on the second row.
        events_second = await _audit_events_for(conn, rows[1][0])
        assert any(e["event"] == "re_escalation_offered" for e in events_second)

    async def test_chain_cap_re_raises_token_error(
        self,
        conn: aiosqlite.Connection,
    ) -> None:
        """Cap=1 means the very first recovery attempt is over cap. The
        original TokenLimitReachedError surfaces to the caller and a
        ``re_escalation_token_limited`` audit row lands on the parent.
        """
        router, repo = await _build_router(
            conn=conn,
            provider_responses=[
                ({"result": "truncated"}, _meta(token_limited=True)),
            ],
            max_depth=0,  # zero allows root only — every re-fire is over cap
        )

        complete_task = asyncio.create_task(
            router.complete(
                prompt="do something",
                task_type="skill_draft",
                task_id="task-1",
                user_id="nick",
                estimate_usd=5.0,
            )
        )
        cid_first = await _wait_for_open_correlation(repo)
        await router._escalation_gate.record_user_resolution(
            correlation_id=cid_first,
            mode="api_extended",
            owner_user_id="nick",
            task_id="task-1",
        )

        with pytest.raises(TokenLimitReachedError):
            await asyncio.wait_for(complete_task, timeout=5.0)

        # Only one row exists — coordinator refused to fire.
        cursor = await conn.execute("SELECT COUNT(*) FROM escalation_request")
        cnt_row = await cursor.fetchone()
        assert cnt_row is not None and cnt_row[0] == 1

        # `re_escalation_token_limited` event keyed off the parent.
        cursor = await conn.execute(
            "SELECT id FROM escalation_request ORDER BY id ASC LIMIT 1"
        )
        first_row = await cursor.fetchone()
        assert first_row is not None
        events = await _audit_events_for(conn, int(first_row[0]))
        assert any(e["event"] == "re_escalation_token_limited" for e in events)

    async def test_recovery_user_picks_pause(
        self,
        conn: aiosqlite.Connection,
    ) -> None:
        """First call truncates → coordinator re-fires → user picks
        pause. Router raises EscalationDecisionError; the original
        token error is replaced by the user's decision."""
        from donna.models.router import EscalationDecisionError

        router, repo = await _build_router(
            conn=conn,
            provider_responses=[
                ({"result": "truncated"}, _meta(token_limited=True)),
            ],
        )

        complete_task = asyncio.create_task(
            router.complete(
                prompt="do something",
                task_type="skill_draft",
                task_id="task-1",
                user_id="nick",
                estimate_usd=5.0,
            )
        )
        cid_first = await _wait_for_open_correlation(repo)
        await router._escalation_gate.record_user_resolution(
            correlation_id=cid_first,
            mode="api_extended",
            owner_user_id="nick",
            task_id="task-1",
        )

        cid_second = await _wait_for_open_correlation(repo)
        for _ in range(50):
            if cid_second != cid_first:
                break
            await asyncio.sleep(0.02)
            cid_second = await _wait_for_open_correlation(repo)
        assert cid_second != cid_first
        await router._escalation_gate.record_user_resolution(
            correlation_id=cid_second,
            mode="pause",
            owner_user_id="nick",
            task_id="task-1",
        )

        with pytest.raises(EscalationDecisionError) as exc_info:
            await asyncio.wait_for(complete_task, timeout=5.0)
        assert exc_info.value.mode == "pause"
