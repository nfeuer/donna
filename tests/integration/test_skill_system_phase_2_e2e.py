"""Phase 2 end-to-end integration test.

Verifies the handoff contract from plan §7 Phase 2:
  H2.1: multi-step skill executes all steps with state accumulation
  H2.2: tool step with for_each fans out and collects results
  H2.3: escalate signal short-circuits a multi-step run
  H2.4: tool failure triggers triage and produces structured result
  H2.5: skill_run + skill_step_result rows persist after execution
  H2.6: ToolRegistry allowlist prevents unauthorized dispatches
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.run_persistence import SkillRunRepository
from donna.skills.tool_registry import ToolRegistry
from donna.skills.triage import TriageDecision, TriageResult


@pytest.fixture
async def db_with_run_tables(tmp_path: Path):
    db_path = tmp_path / "phase2.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
            task_id TEXT, automation_run_id TEXT, status TEXT NOT NULL,
            total_latency_ms INTEGER, total_cost_usd REAL,
            state_object TEXT NOT NULL, tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE skill_step_result (
            id TEXT PRIMARY KEY, skill_run_id TEXT NOT NULL,
            step_name TEXT NOT NULL, step_index INTEGER NOT NULL,
            step_kind TEXT NOT NULL, invocation_log_id TEXT,
            prompt_tokens INTEGER, output TEXT, tool_calls TEXT,
            latency_ms INTEGER, validation_status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _skill() -> SkillRow:
    return SkillRow(
        id="s1", capability_name="demo", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


def _version(yaml_backbone: str, step_content: dict, output_schemas: dict) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None,
        created_at=datetime.now(UTC),
    )


def _mock_meta(invocation_id="i1"):
    return MagicMock(
        invocation_id=invocation_id, latency_ms=50,
        tokens_in=20, tokens_out=5, cost_usd=0.0,
    )


@pytest.mark.integration
async def test_h2_1_multistep_skill_accumulates_state(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: step_a
    kind: llm
    prompt: pa.md
    output_schema: sa.json
  - name: step_b
    kind: llm
    prompt: pb.md
    output_schema: sb.json
final_output: "{{ state.step_b }}"
"""
    version = _version(
        yaml_backbone,
        step_content={
            "step_a": "Extract from: {{ inputs.raw }}",
            "step_b": "Classify: {{ state.step_a.title }}",
        },
        output_schemas={
            "step_a": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            "step_b": {
                "type": "object",
                "properties": {"priority": {"type": "integer"}},
                "required": ["priority"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"title": "review"}, _mock_meta("i1")),
        ({"priority": 2}, _mock_meta("i2")),
    ]

    repo = SkillRunRepository(db_with_run_tables)
    executor = SkillExecutor(router, ToolRegistry(), triage=None, run_repository=repo)

    result = await executor.execute(
        skill=_skill(), version=version,
        inputs={"raw": "draft the review"}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["step_a"]["title"] == "review"
    assert result.state["step_b"]["priority"] == 2


@pytest.mark.integration
async def test_h2_2_for_each_fan_out(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: fetch_many
    kind: tool
    tools: [mock_tool]
    tool_invocations:
      - for_each: "{{ inputs.urls }}"
        as: url
        tool: mock_tool
        args: {u: "{{ url }}"}
        store_as: "r{{ loop.index0 }}"
final_output: "{{ state.fetch_many }}"
"""
    version = _version(yaml_backbone, step_content={}, output_schemas={})

    async def mock_tool(u: str):
        return {"got": u}

    registry = ToolRegistry()
    registry.register("mock_tool", mock_tool)
    executor = SkillExecutor(
        AsyncMock(), registry, triage=None,
        run_repository=SkillRunRepository(db_with_run_tables),
    )

    result = await executor.execute(
        skill=_skill(), version=version,
        inputs={"urls": ["a", "b", "c"]}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["fetch_many"]["r0"] == {"got": "a"}
    assert result.state["fetch_many"]["r2"] == {"got": "c"}


@pytest.mark.integration
async def test_h2_3_escalate_short_circuits(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: s1
    kind: llm
    prompt: p.md
    output_schema: s.json
  - name: s2
    kind: llm
    prompt: p2.md
    output_schema: s2.json
final_output: "{{ state.s2 }}"
"""
    version = _version(
        yaml_backbone,
        step_content={"s1": "x", "s2": "y"},
        output_schemas={
            "s1": {
                "type": "object",
                "properties": {"escalate": {"type": "object"}},
            },
            "s2": {"type": "object"},
        },
    )

    router = AsyncMock()
    router.complete.return_value = ({"escalate": {"reason": "no idea"}}, _mock_meta("i1"))

    executor = SkillExecutor(
        router, ToolRegistry(), triage=None,
        run_repository=SkillRunRepository(db_with_run_tables),
    )
    result = await executor.execute(
        skill=_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "no idea"
    assert router.complete.call_count == 1


@pytest.mark.integration
async def test_h2_4_tool_failure_triggers_triage(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: tool_step
    kind: tool
    tools: [failing_tool]
    tool_invocations:
      - tool: failing_tool
        args: {}
        retry: {max_attempts: 2, backoff_s: [0, 0]}
        store_as: result
final_output: "{{ state.tool_step }}"
"""
    version = _version(yaml_backbone, step_content={}, output_schemas={})

    async def failing_tool(**kwargs):
        raise RuntimeError("cannot reach endpoint")

    registry = ToolRegistry()
    registry.register("failing_tool", failing_tool)

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.ESCALATE_TO_CLAUDE,
        rationale="tool unreachable",
    )

    executor = SkillExecutor(
        AsyncMock(), registry, triage=triage,
        run_repository=SkillRunRepository(db_with_run_tables),
    )

    result = await executor.execute(
        skill=_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "escalated"
    triage.handle_failure.assert_awaited_once()


@pytest.mark.integration
async def test_h2_5_persistence_writes_rows(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: only
    kind: llm
    prompt: p.md
    output_schema: s.json
final_output: "{{ state.only }}"
"""
    version = _version(
        yaml_backbone,
        step_content={"only": "prompt"},
        output_schemas={
            "only": {
                "type": "object",
                "properties": {"v": {"type": "integer"}},
                "required": ["v"],
            },
        },
    )

    router = AsyncMock()
    router.complete.return_value = ({"v": 42}, _mock_meta("i1"))

    repo = SkillRunRepository(db_with_run_tables)
    executor = SkillExecutor(router, ToolRegistry(), triage=None, run_repository=repo)

    await executor.execute(skill=_skill(), version=version, inputs={}, user_id="nick")

    cursor = await db_with_run_tables.execute("SELECT status, final_output FROM skill_run")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "succeeded"

    cursor = await db_with_run_tables.execute("SELECT step_name FROM skill_step_result")
    step_rows = await cursor.fetchall()
    assert len(step_rows) == 1
    assert step_rows[0][0] == "only"


@pytest.mark.integration
async def test_h2_6_allowlist_enforced(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: unauthorized
    kind: tool
    tools: [allowed_tool]
    tool_invocations:
      - tool: forbidden_tool
        args: {}
        store_as: r
final_output: "{}"
"""
    version = _version(yaml_backbone, step_content={}, output_schemas={})

    async def forbidden(**kwargs):
        return {"v": 1}

    registry = ToolRegistry()
    registry.register("forbidden_tool", forbidden)

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.ESCALATE_TO_CLAUDE,
        rationale="tool not allowed",
    )

    executor = SkillExecutor(
        AsyncMock(), registry, triage=triage,
        run_repository=SkillRunRepository(db_with_run_tables),
    )

    result = await executor.execute(
        skill=_skill(), version=version, inputs={}, user_id="nick",
    )

    # Result should be escalated (triage caught the ToolInvocationError).
    assert result.status == "escalated"
