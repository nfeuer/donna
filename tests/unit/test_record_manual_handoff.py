"""Unit tests for EscalationGate.record_manual_handoff (slice 21).

Realizes acceptance for docs/superpowers/specs/manual-escalation.md
§5.3 — the user click path that writes the spec file, mirrors the body
into the row, and resolves the escalation as ``claude_code``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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
from donna.cost.claude_code_spec import ClaudeCodeSpecBuilder
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import ESCALATION_TASK_TYPE
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tracker import CostTracker

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
    parent_escalation_id INTEGER,
    human_review INTEGER NOT NULL DEFAULT 0,
    target_paths TEXT,
    originating_entity_type TEXT,
    originating_entity_id TEXT,
    base_sha TEXT,
    merged_at TEXT
);
CREATE TABLE skill_candidate_report (
    id TEXT PRIMARY KEY,
    capability_name TEXT NOT NULL
);
CREATE TABLE skill (
    id TEXT PRIMARY KEY,
    capability_name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'claude_native'
);
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER
);
CREATE TABLE dashboard_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE daily_budget_extension (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    escalation_request_id INTEGER,
    voided INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "handoff.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def task_types() -> TaskTypesConfig:
    return TaskTypesConfig(
        task_types={
            "skill_auto_draft": TaskTypeEntry(
                description="auto draft",
                model="reasoner",
                prompt_template="prompts/skill_auto_draft.md",
                output_schema="schemas/skill_auto_draft_output.json",
                tools=[],
                manual_escalation=ManualEscalationTaskTypeConfig(
                    mode="claude_code",
                    target_paths={
                        "skill": "skills/{name}/**",
                        "fixtures": "fixtures/{name}/**",
                    },
                    reference_module="skills/parse_task/skill.yaml",
                    forbidden_patterns=["import anthropic"],
                ),
            ),
        }
    )


@pytest.fixture
def manual_escalation_config() -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(),
        budget_extension=BudgetExtensionConfig(),
    )


@pytest.fixture
def spec_builder(tmp_path: Path) -> ClaudeCodeSpecBuilder:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return ClaudeCodeSpecBuilder(
        prompt_dir=Path("prompts/escalation"),
        workspace_path=workspace,
        host_repo_path=tmp_path / "host",
        worktree_root=workspace / "worktrees",
        dashboard_base_url="http://localhost:8080",
    )


@pytest.fixture
def gate(
    conn: aiosqlite.Connection,
    task_types: TaskTypesConfig,
    manual_escalation_config: ManualEscalationConfig,
    spec_builder: ClaudeCodeSpecBuilder,
) -> EscalationGate:
    repo = EscalationRepository(conn)
    tracker = CostTracker(conn)
    resolver = DashboardSettingResolver(repo)
    deliver: Any = AsyncMock(return_value=True)
    return EscalationGate(
        repository=repo,
        tracker=tracker,
        config=manual_escalation_config,
        daily_pause_threshold_usd=20.0,
        resolver=resolver,
        deliver=deliver,
        extension_repo=BudgetExtensionRepository(conn),
        spec_builder=spec_builder,
        task_types_config=task_types,
        host_repo=None,
    )


async def _seed_open_row(
    conn: aiosqlite.Connection,
    *,
    correlation_id: str = "cc-1",
    capability: str = "bookmark",
) -> int:
    # Insert a skill_candidate_report so the gate can resolve the
    # capability name from originating_entity_id.
    await conn.execute(
        "INSERT INTO skill_candidate_report (id, capability_name) VALUES (?, ?)",
        ("cand-1", capability),
    )
    cur = await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_type, estimate_usd, daily_remaining_usd,
            offered_modes, status, iteration, created_at, priority,
            originating_entity_type, originating_entity_id, base_sha
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "nick", correlation_id, "skill_auto_draft", 7.5, 1.0,
            json.dumps(["claude_code", "pause", "cancel"]),
            "open", 1, "2026-05-06T12:00:00+00:00", 2,
            "skill_candidate_report", "cand-1", "abc1234567",
        ),
    )
    await conn.commit()
    rid = cur.lastrowid
    assert rid is not None
    return int(rid)


async def test_record_manual_handoff_writes_spec_and_resolves(
    gate: EscalationGate, conn: aiosqlite.Connection
) -> None:
    rid = await _seed_open_row(conn)

    rendered = await gate.record_manual_handoff(
        correlation_id="cc-1",
        mode="claude_code",
        actor_id="999",
    )
    assert rendered is not None
    assert rendered.path.is_file()
    assert "bookmark" in rendered.body
    assert rendered.target_paths == {
        "skill": "skills/bookmark/**",
        "fixtures": "fixtures/bookmark/**",
    }

    cur = await conn.execute(
        "SELECT status, mode, prompt_path, prompt_body, resolution "
        "FROM escalation_request WHERE id = ?",
        (rid,),
    )
    status, mode, prompt_path, prompt_body, resolution = await cur.fetchone()
    assert status == "resolved"
    assert mode == "claude_code"
    assert prompt_path == str(rendered.path)
    assert prompt_body == rendered.body
    assert resolution == "claude_code"


async def test_record_manual_handoff_writes_audit_event(
    gate: EscalationGate, conn: aiosqlite.Connection
) -> None:
    await _seed_open_row(conn)
    await gate.record_manual_handoff(
        correlation_id="cc-1", mode="claude_code", actor_id="999"
    )
    cur = await conn.execute(
        "SELECT output FROM invocation_log WHERE task_type = ? ORDER BY timestamp ASC",
        (ESCALATION_TASK_TYPE,),
    )
    rows = await cur.fetchall()
    assert any(
        json.loads(r[0]).get("mode") == "claude_code"
        and json.loads(r[0]).get("event") == "escalation_resolved"
        for r in rows
    )


async def test_record_manual_handoff_unsupported_mode_returns_none(
    gate: EscalationGate, conn: aiosqlite.Connection
) -> None:
    await _seed_open_row(conn)
    rendered = await gate.record_manual_handoff(
        correlation_id="cc-1", mode="chat",  # not claude_code
    )
    assert rendered is None


async def test_record_manual_handoff_missing_originating_entity(
    gate: EscalationGate, conn: aiosqlite.Connection
) -> None:
    """Returns None gracefully if capability name can't be resolved."""
    cur = await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_type, estimate_usd, daily_remaining_usd,
            offered_modes, status, iteration, created_at, priority
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("nick", "cc-no-ent", "skill_auto_draft", 7.5, 1.0,
         json.dumps(["claude_code", "pause"]), "open", 1,
         "2026-05-06T12:00:00+00:00", 2),
    )
    await conn.commit()
    assert cur.lastrowid is not None
    rendered = await gate.record_manual_handoff(
        correlation_id="cc-no-ent", mode="claude_code",
    )
    assert rendered is None
