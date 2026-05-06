"""Slice 20 tests for chat-mode wiring on the EscalationGate.

Covers the four-pronged eligibility check from
``docs/superpowers/specs/manual-escalation.md`` §5.2 / §6.2 + the
``original_prompt`` flow through to the chat prompt builder.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import (
    ClaudeCodeModeConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTaskTypeConfig,
    ManualEscalationTriggersConfig,
    TaskTypeEntry,
    TaskTypesConfig,
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


def _config(*, chat_enabled: bool = True) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=chat_enabled),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(task_approval_threshold_usd=5.0),
    )


def _task_types(*, with_chat: bool = True) -> TaskTypesConfig:
    chat_entry = TaskTypeEntry(
        description="Chat escalation",
        model="parser",
        prompt_template="prompts/chat/chat_respond.md",
        output_schema="schemas/chat_respond_output.json",
        manual_escalation=ManualEscalationTaskTypeConfig(mode="chat")
        if with_chat
        else None,
    )
    plain_entry = TaskTypeEntry(
        description="Plain task",
        model="parser",
        prompt_template="prompts/parse_task.md",
        output_schema="schemas/task_parse_output.json",
    )
    return TaskTypesConfig(task_types={
        "chat_escalation": chat_entry,
        "plain_task": plain_entry,
    })


def _tracker(daily_total: float = 0.0) -> CostTracker:
    t = MagicMock(spec=CostTracker)
    t.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_total, call_count=0, breakdown={})
    )
    return t


def _stub_extension_repo() -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=0.0)
    stub.get_monthly_total = AsyncMock(return_value=0.0)
    return stub


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "gate_chat.db"))
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
    config: ManualEscalationConfig | None = None,
    task_types_config: TaskTypesConfig | None = None,
    chat_prompt_builder: MagicMock | None = None,
    deliver_returns: bool = True,
) -> tuple[EscalationGate, AsyncMock]:
    deliver = AsyncMock(return_value=deliver_returns)
    gate = EscalationGate(
        repository=repo,
        tracker=_tracker(),
        config=config or _config(),
        daily_pause_threshold_usd=20.0,
        resolver=DashboardSettingResolver(repo),
        deliver=deliver,
        extension_repo=_stub_extension_repo(),
        task_types_config=task_types_config,
        chat_prompt_builder=chat_prompt_builder,
    )
    return gate, deliver


async def _await_correlation_id(repo: EscalationRepository) -> str:
    for _ in range(100):
        cur = await repo._conn.execute(
            "SELECT correlation_id FROM escalation_request LIMIT 1"
        )
        row = await cur.fetchone()
        if row is not None:
            return row[0]
        await asyncio.sleep(0.01)
    raise AssertionError("gate did not create a row")


# ---------------------------------------------------------------------
# offered_modes shape
# ---------------------------------------------------------------------


class TestOfferedModes:
    async def test_chat_added_when_eligible(
        self, repo: EscalationRepository
    ) -> None:
        builder = MagicMock()
        builder.build_and_persist = AsyncMock(return_value=("body", "summary", "/path"))

        gate, _deliver = _gate(
            repo=repo,
            task_types_config=_task_types(with_chat=True),
            chat_prompt_builder=builder,
        )
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="chat_escalation",
                estimate_usd=8.0,
                original_prompt="please answer this for me",
            )
        )
        cid = await _await_correlation_id(repo)
        cur = await repo._conn.execute(
            "SELECT offered_modes FROM escalation_request"
        )
        row = await cur.fetchone()
        assert row is not None
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id="t1",
        )
        await asyncio.wait_for(task, timeout=2.0)

        import json
        modes = json.loads(row[0])
        assert "chat" in modes
        assert "pause" in modes
        assert "cancel" in modes
        # api_extended slot is between extension and pause when both eligible.
        if "api_extended" in modes:
            assert modes.index("chat") > modes.index("api_extended")
        assert modes.index("chat") < modes.index("pause")
        builder.build_and_persist.assert_awaited_once()

    async def test_chat_omitted_when_no_original_prompt(
        self, repo: EscalationRepository
    ) -> None:
        builder = MagicMock()
        builder.build_and_persist = AsyncMock()
        gate, _deliver = _gate(
            repo=repo,
            task_types_config=_task_types(with_chat=True),
            chat_prompt_builder=builder,
        )
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="chat_escalation",
                estimate_usd=8.0,
            )
        )
        cid = await _await_correlation_id(repo)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id="t1",
        )
        await asyncio.wait_for(task, timeout=2.0)

        cur = await repo._conn.execute(
            "SELECT offered_modes FROM escalation_request"
        )
        row = await cur.fetchone()
        assert row is not None
        import json
        modes = json.loads(row[0])
        assert "chat" not in modes
        builder.build_and_persist.assert_not_awaited()

    async def test_chat_omitted_when_task_type_not_chat(
        self, repo: EscalationRepository
    ) -> None:
        builder = MagicMock()
        builder.build_and_persist = AsyncMock()
        gate, _deliver = _gate(
            repo=repo,
            task_types_config=_task_types(with_chat=True),
            chat_prompt_builder=builder,
        )
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="plain_task",
                estimate_usd=8.0,
                original_prompt="anything",
            )
        )
        cid = await _await_correlation_id(repo)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id="t1",
        )
        await asyncio.wait_for(task, timeout=2.0)

        cur = await repo._conn.execute(
            "SELECT offered_modes FROM escalation_request"
        )
        row = await cur.fetchone()
        assert row is not None
        import json
        modes = json.loads(row[0])
        assert "chat" not in modes

    async def test_chat_omitted_when_chat_mode_disabled_in_yaml(
        self, repo: EscalationRepository
    ) -> None:
        builder = MagicMock()
        builder.build_and_persist = AsyncMock()
        gate, _deliver = _gate(
            repo=repo,
            config=_config(chat_enabled=False),
            task_types_config=_task_types(with_chat=True),
            chat_prompt_builder=builder,
        )
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="chat_escalation",
                estimate_usd=8.0,
                original_prompt="anything",
            )
        )
        cid = await _await_correlation_id(repo)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id="t1",
        )
        await asyncio.wait_for(task, timeout=2.0)

        cur = await repo._conn.execute(
            "SELECT offered_modes FROM escalation_request"
        )
        row = await cur.fetchone()
        assert row is not None
        import json
        modes = json.loads(row[0])
        assert "chat" not in modes


# ---------------------------------------------------------------------
# Resolution path
# ---------------------------------------------------------------------


class TestChatResolution:
    async def test_chat_resolution_returns_chat_outcome(
        self, repo: EscalationRepository
    ) -> None:
        builder = MagicMock()
        builder.build_and_persist = AsyncMock(return_value=("body", "summary", "/path"))
        gate, _deliver = _gate(
            repo=repo,
            task_types_config=_task_types(with_chat=True),
            chat_prompt_builder=builder,
        )
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="chat_escalation",
                estimate_usd=8.0,
                original_prompt="please answer",
            )
        )
        cid = await _await_correlation_id(repo)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="chat",
            owner_user_id="nick",
            task_id="t1",
        )
        outcome = await asyncio.wait_for(task, timeout=2.0)
        assert outcome.fired is True
        assert outcome.mode == "chat"
        assert outcome.resolved_by == "user"

    async def test_prompt_builder_failure_is_non_fatal(
        self, repo: EscalationRepository
    ) -> None:
        builder = MagicMock()
        builder.build_and_persist = AsyncMock(side_effect=RuntimeError("boom"))
        gate, _deliver = _gate(
            repo=repo,
            task_types_config=_task_types(with_chat=True),
            chat_prompt_builder=builder,
        )
        task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="t1",
                task_type="chat_escalation",
                estimate_usd=8.0,
                original_prompt="please answer",
            )
        )
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
        # The row still exists despite the prompt builder exploding.
        cur = await repo._conn.execute(
            "SELECT status FROM escalation_request WHERE correlation_id = ?",
            (cid,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "resolved"
