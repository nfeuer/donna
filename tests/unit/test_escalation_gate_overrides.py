"""Slice 23 — per-task-type override grid + slider integration tests.

The gate's offered_modes builder reads three new things through the
:class:`DashboardSettingResolver`:

1. ``manual_escalation.task_types.<task_type>.override`` —
   ``auto`` / ``force_api`` / ``force_manual`` / ``disabled``.
2. ``manual_escalation.budget_extension.max_daily_extension_usd`` —
   the slider value, which can lower the daily extension headroom
   below the YAML default.
3. The legacy keys remain functional via the resolver's alias fallback
   (covered in :mod:`tests.unit.test_dashboard_settings_writes`).

Spec: docs/superpowers/specs/manual-escalation.md §6.3(a) / §10.7.
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
from donna.cost.dashboard_settings_catalog import task_type_override_key
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
CREATE TABLE daily_budget_extension (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    escalation_request_id INTEGER,
    voided INTEGER NOT NULL DEFAULT 0
);
"""


def _config(
    *,
    extension_enabled: bool = True,
    max_daily_extension_usd: float = 10.0,
) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        budget_extension=BudgetExtensionConfig(
            enabled=extension_enabled,
            max_daily_extension_usd=max_daily_extension_usd,
            hard_monthly_ceiling_usd=150.0,
        ),
        triggers=ManualEscalationTriggersConfig(task_approval_threshold_usd=5.0),
    )


def _task_types_with_chat() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "chat_escalation": TaskTypeEntry(
                description="x",
                model="parser",
                prompt_template="prompts/x.md",
                output_schema="schemas/x.json",
                manual_escalation=ManualEscalationTaskTypeConfig(mode="chat"),
            ),
        }
    )


def _tracker() -> CostTracker:
    t = MagicMock(spec=CostTracker)
    t.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
    )
    return t


def _stub_extension_repo(*, daily_total: float = 0.0) -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=daily_total)
    stub.get_monthly_total = AsyncMock(return_value=0.0)
    return stub


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "gate_overrides.db"))
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
    extension_repo: MagicMock | None = None,
) -> EscalationGate:
    builder = MagicMock()
    builder.build_and_persist = AsyncMock(
        return_value=("body", "summary", "/path")
    )
    return EscalationGate(
        repository=repo,
        tracker=_tracker(),
        config=config or _config(),
        daily_pause_threshold_usd=20.0,
        resolver=DashboardSettingResolver(repo),
        deliver=AsyncMock(return_value=True),
        extension_repo=extension_repo or _stub_extension_repo(),
        task_types_config=task_types_config or _task_types_with_chat(),
        chat_prompt_builder=builder,
    )


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


async def _drive_to_resolution(
    *, repo: EscalationRepository, gate: EscalationGate, task_type: str
) -> list[str]:
    """Fire an escalation, capture offered_modes, then resolve as pause."""
    task = asyncio.create_task(
        gate.fire_and_wait(
            user_id="nick",
            task_id="t1",
            task_type=task_type,
            estimate_usd=8.0,
            original_prompt="please answer",
        )
    )
    cid = await _await_correlation_id(repo)
    cur = await repo._conn.execute(
        "SELECT offered_modes FROM escalation_request WHERE correlation_id = ?",
        (cid,),
    )
    row = await cur.fetchone()
    assert row is not None
    modes: list[str] = json.loads(row[0])

    await gate.record_user_resolution(
        correlation_id=cid, mode="pause", owner_user_id="nick", task_id="t1"
    )
    await asyncio.wait_for(task, timeout=2.0)
    return modes


# ---------------------------------------------------------------------------
# Per-task-type override grid
# ---------------------------------------------------------------------------


class TestTaskTypeOverride:
    async def test_default_auto_offers_chat(
        self, repo: EscalationRepository
    ) -> None:
        modes = await _drive_to_resolution(
            repo=repo, gate=_gate(repo=repo), task_type="chat_escalation"
        )
        assert "chat" in modes
        assert "api_extended" in modes  # default has full headroom
        assert "pause" in modes and "cancel" in modes

    async def test_disabled_falls_through_to_pause_cancel(
        self, repo: EscalationRepository
    ) -> None:
        await repo.upsert_dashboard_setting(
            task_type_override_key("chat_escalation"), "disabled"
        )
        modes = await _drive_to_resolution(
            repo=repo, gate=_gate(repo=repo), task_type="chat_escalation"
        )
        assert "chat" not in modes
        assert "api_extended" not in modes
        assert "claude_code" not in modes
        assert modes == ["pause", "cancel"]

    async def test_force_api_hides_manual_handoff(
        self, repo: EscalationRepository
    ) -> None:
        await repo.upsert_dashboard_setting(
            task_type_override_key("chat_escalation"), "force_api"
        )
        modes = await _drive_to_resolution(
            repo=repo, gate=_gate(repo=repo), task_type="chat_escalation"
        )
        assert "chat" not in modes
        assert "claude_code" not in modes
        assert "api_extended" in modes

    async def test_force_manual_hides_api_extended(
        self, repo: EscalationRepository
    ) -> None:
        await repo.upsert_dashboard_setting(
            task_type_override_key("chat_escalation"), "force_manual"
        )
        modes = await _drive_to_resolution(
            repo=repo, gate=_gate(repo=repo), task_type="chat_escalation"
        )
        assert "api_extended" not in modes
        assert "chat" in modes


# ---------------------------------------------------------------------------
# max_daily_extension_usd slider
# ---------------------------------------------------------------------------


class TestSliderResolution:
    async def test_slider_overrides_yaml_default_lower(
        self, repo: EscalationRepository
    ) -> None:
        """Lowering the slider below the estimate hides the api button.

        Estimate $8 fits within YAML default ($10) but not within the
        $5 dashboard override.
        """
        await repo.upsert_dashboard_setting(
            "manual_escalation.budget_extension.max_daily_extension_usd",
            5.0,
        )
        gate = _gate(repo=repo)
        modes = await _drive_to_resolution(
            repo=repo, gate=gate, task_type="chat_escalation"
        )
        assert "api_extended" not in modes
        # chat still offered.
        assert "chat" in modes
