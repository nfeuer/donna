"""Tests for EscalationGate.open_tool_build_escalation (slice 22)."""

from __future__ import annotations

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
    ToolGapConfig,
    ToolGapLintConfigModel,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.claude_code_spec import RenderedSpec
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tool_request_repository import ToolRequestRepository
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
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER
);
CREATE TABLE tool_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    proposed_signature TEXT,
    rationale TEXT,
    blocking_capability_id TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL DEFAULT 'speculative',
    detection_point TEXT,
    snoozed_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_branch TEXT,
    escalation_request_id INTEGER,
    last_pinged_at TEXT
);
"""


def _config() -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=5.0,
            escalation_timeout_minutes=60,
            manual_iteration_limit=3,
        ),
        tool_gap=ToolGapConfig(
            realtime_channel="agents",
            snooze_seconds=86400,
            reping_cooldown_seconds=14400,
            lint=ToolGapLintConfigModel(
                requires_rebuild_default=False,
                default_timeout_seconds=5,
            ),
        ),
    )


def _task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "tool_request_fulfillment": TaskTypeEntry(
                description="manual tool build",
                model="reasoner",
                prompt_template="prompts/escalation/tool_build.md",
                output_schema="schemas/escalation_submission.json",
                tools=[],
                manual_escalation=ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={
                        "tool": "src/donna/skills/tools/{name}.py",
                        "tool_test": "tests/skills/tools/test_{name}.py",
                    },
                    reference_module="src/donna/skills/tools/{name}.py",
                    forbidden_patterns=["import anthropic"],
                ),
            ),
        },
    )


def _tracker(daily_total: float) -> CostTracker:
    tracker = MagicMock(spec=CostTracker)
    tracker.get_daily_cost = AsyncMock(
        return_value=CostSummary(total_usd=daily_total, call_count=0, breakdown={})
    )
    return tracker


def _stub_extension_repo() -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=0.0)
    stub.get_monthly_total = AsyncMock(return_value=0.0)
    return stub


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "gate-tool.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def gate_with_repo(conn):
    repo = EscalationRepository(conn)
    tool_request_repo = ToolRequestRepository(conn)
    spec_builder = MagicMock()
    spec_builder.render = MagicMock(
        return_value=RenderedSpec(
            body="# spec",
            path=Path("/tmp/spec.md"),
            branch_name="escalation/abcd1234-fooz",
            target_paths={"tool": "src/donna/skills/tools/fooz.py"},
            worktree_command="git worktree add ...",
        )
    )
    host_repo = MagicMock()
    host_repo.rev_parse = AsyncMock(return_value="deadbeefdeadbeef")
    deliver = AsyncMock(return_value=True)
    gate = EscalationGate(
        repository=repo,
        tracker=_tracker(0.0),
        config=_config(),
        daily_pause_threshold_usd=20.0,
        resolver=DashboardSettingResolver(repo),
        deliver=deliver,
        extension_repo=_stub_extension_repo(),
        task_types_config=_task_types_config(),
        spec_builder=spec_builder,
        host_repo=host_repo,
    )
    return gate, repo, tool_request_repo, spec_builder


async def _seed_tool_request(repo, *, tool_name="fooz") -> int:
    from donna.cost.tool_gap import (
        DETECTION_AUTOMATION_CREATE,
        SEVERITY_HIGH,
        ToolGap,
    )
    res = await repo.record(
        ToolGap(
            tool_name=tool_name,
            user_id="nick",
            severity=SEVERITY_HIGH,
            blocking_capability_id="news_check",
            rationale="cap blocked",
            proposed_signature={
                "name": tool_name,
                "params": [{"name": "x", "type": "str", "required": True}],
                "returns": "dict",
                "summary": "test summary",
            },
            detection_point=DETECTION_AUTOMATION_CREATE,
        )
    )
    return res.row.id


@pytest.mark.asyncio
async def test_open_tool_build_creates_row_and_renders_spec(gate_with_repo):
    gate, esc_repo, tool_repo, spec_builder = gate_with_repo
    request_id = await _seed_tool_request(tool_repo)

    esc_row, rendered = await gate.open_tool_build_escalation(
        tool_request_id=request_id,
        tool_name="fooz",
        user_id="nick",
        priority=3,
        actor_id="discord-1",
        proposed_signature=None,
    )
    assert esc_row.task_type == "tool_request_fulfillment"
    assert esc_row.originating_entity_type == "tool_request"
    assert esc_row.originating_entity_id == str(request_id)
    assert "claude_code" in esc_row.offered_modes
    assert rendered is not None
    assert rendered.branch_name.startswith("escalation/")
    spec_builder.render.assert_called_once()
    # extra_context passed to render — confirms config plumbing.
    call_kwargs = spec_builder.render.call_args.kwargs
    assert "extra_context" in call_kwargs
    assert call_kwargs["extra_context"]["requires_rebuild_default"] is False
    assert call_kwargs["extra_context"]["default_timeout_seconds"] == 5


@pytest.mark.asyncio
async def test_open_tool_build_dedups_existing_open_escalation(gate_with_repo):
    gate, esc_repo, tool_repo, spec_builder = gate_with_repo
    request_id = await _seed_tool_request(tool_repo)

    first_row, first_rendered = await gate.open_tool_build_escalation(
        tool_request_id=request_id,
        tool_name="fooz",
        user_id="nick",
    )
    spec_builder.render.reset_mock()
    second_row, second_rendered = await gate.open_tool_build_escalation(
        tool_request_id=request_id,
        tool_name="fooz",
        user_id="nick",
    )
    assert second_row.id == first_row.id
    assert second_rendered is None
    spec_builder.render.assert_not_called()


@pytest.mark.asyncio
async def test_open_tool_build_aborts_when_disabled(conn):
    repo = EscalationRepository(conn)
    tool_repo = ToolRequestRepository(conn)
    cfg = _config()
    cfg.enabled = False
    gate = EscalationGate(
        repository=repo,
        tracker=_tracker(0.0),
        config=cfg,
        daily_pause_threshold_usd=20.0,
        resolver=DashboardSettingResolver(repo),
        deliver=AsyncMock(return_value=True),
        extension_repo=_stub_extension_repo(),
        task_types_config=_task_types_config(),
    )
    request_id = await _seed_tool_request(tool_repo)
    row, rendered = await gate.open_tool_build_escalation(
        tool_request_id=request_id, tool_name="fooz", user_id="nick"
    )
    assert rendered is None
    # The synthetic row was created + immediately resolved=cancel.
    refreshed = await repo.get(row.id)
    assert refreshed.status == "resolved"
    assert refreshed.resolution == "cancel"
