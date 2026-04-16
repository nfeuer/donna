from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.models import SkillRow, SkillVersionRow


def _make_skill() -> SkillRow:
    return SkillRow(
        id="s1", capability_name="parse_task", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )


def _make_version(step_content: dict, output_schemas: dict, yaml_backbone: str | None = None) -> SkillVersionRow:
    if yaml_backbone is None:
        step_names = list(step_content.keys())
        steps_yaml = "\n".join(
            f"  - name: {name}\n    kind: llm\n    prompt: steps/{name}.md\n    output_schema: schemas/{name}_v1.json"
            for name in step_names
        )
        yaml_backbone = f"capability_name: parse_task\nversion: 1\nsteps:\n{steps_yaml}\nfinal_output: '{{{{ state.{step_names[0]} }}}}'"

    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1, yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None, created_at=datetime.now(timezone.utc),
    )


def _mock_meta(**kwargs):
    defaults = {"invocation_id": "inv-1", "latency_ms": 100, "tokens_in": 50, "tokens_out": 20, "cost_usd": 0.0}
    defaults.update(kwargs)
    return MagicMock(**defaults)


async def test_executor_runs_single_step_skill():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["title", "confidence"],
        }},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"title": "Draft Q2 review", "confidence": 0.9},
        _mock_meta(),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "draft the Q2 review by Friday"},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.final_output == {"title": "Draft Q2 review", "confidence": 0.9}
    assert "extract" in result.state
    assert result.state["extract"]["title"] == "Draft Q2 review"


async def test_executor_handles_escalate_signal():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "escalate": {"type": "object"},
            },
        }},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"escalate": {"reason": "insufficient info"}},
        _mock_meta(invocation_id="inv-2"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "???"},
        user_id="nick",
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "insufficient info"


async def test_executor_fails_on_schema_validation_error():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"not_title": "x"},
        _mock_meta(invocation_id="inv-3"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "foo"},
        user_id="nick",
    )

    assert result.status == "failed"
    assert "title" in result.error


async def test_executor_fails_on_model_exception():
    version = _make_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {"type": "object"}},
    )

    router = AsyncMock()
    router.complete.side_effect = RuntimeError("model_unavailable")

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "foo"},
        user_id="nick",
    )

    assert result.status == "failed"
    assert "model_call" in result.error


async def test_executor_handles_empty_steps():
    version = _make_version(
        step_content={},
        output_schemas={},
        yaml_backbone="capability_name: parse_task\nversion: 1\nsteps: []\n",
    )

    router = AsyncMock()
    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.final_output == {}
    router.complete.assert_not_called()


# --- Phase 2 multi-step tests ---

from donna.skills.tool_registry import ToolRegistry
from donna.skills.triage import TriageAgent, TriageDecision, TriageResult


def _multistep_version(yaml_backbone: str, step_content: dict, output_schemas: dict) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None,
        created_at=datetime.now(timezone.utc),
    )


async def test_executor_runs_two_step_skill():
    yaml_backbone = """
capability_name: parse_task
version: 1
steps:
  - name: extract
    kind: llm
    prompt: steps/extract.md
    output_schema: schemas/extract_v1.json
  - name: classify
    kind: llm
    prompt: steps/classify.md
    output_schema: schemas/classify_v1.json
final_output: "{{ state.classify }}"
"""

    version = _multistep_version(
        yaml_backbone,
        step_content={
            "extract": "Extract: {{ inputs.raw_text }}",
            "classify": "Classify: {{ state.extract.title }}",
        },
        output_schemas={
            "extract": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            "classify": {"type": "object", "properties": {"priority": {"type": "integer"}}, "required": ["priority"]},
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"title": "Q2 review"}, _mock_meta(invocation_id="i1")),
        ({"priority": 3}, _mock_meta(invocation_id="i2")),
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "draft the Q2 review"},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["extract"]["title"] == "Q2 review"
    assert result.state["classify"]["priority"] == 3
    assert result.final_output == {"priority": 3}
    assert router.complete.call_count == 2


async def test_executor_runs_tool_step_with_for_each():
    yaml_backbone = """
capability_name: fetch
version: 1
steps:
  - name: fetch_all
    kind: tool
    tools: [mock_fetch]
    tool_invocations:
      - for_each: "{{ inputs.urls }}"
        as: url
        tool: mock_fetch
        args:
          u: "{{ url }}"
        store_as: "fetched_{{ loop.index0 }}"
final_output: "{{ state.fetch_all }}"
"""
    version = _multistep_version(yaml_backbone, step_content={}, output_schemas={})

    async def mock_fetch(u: str):
        return {"url_fetched": u}

    registry = ToolRegistry()
    registry.register("mock_fetch", mock_fetch)

    executor = SkillExecutor(AsyncMock(), tool_registry=registry)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"urls": ["https://a.com", "https://b.com"]},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["fetch_all"]["fetched_0"] == {"url_fetched": "https://a.com"}
    assert result.state["fetch_all"]["fetched_1"] == {"url_fetched": "https://b.com"}


async def test_executor_escalate_signal_short_circuits_multistep():
    yaml_backbone = """
capability_name: parse
version: 1
steps:
  - name: first
    kind: llm
    prompt: p.md
    output_schema: s.json
  - name: second
    kind: llm
    prompt: p2.md
    output_schema: s2.json
final_output: "{{ state.second }}"
"""
    version = _multistep_version(
        yaml_backbone,
        step_content={"first": "...", "second": "..."},
        output_schemas={
            "first": {"type": "object", "properties": {"escalate": {"type": "object"}}},
            "second": {"type": "object"},
        },
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"escalate": {"reason": "insufficient context"}},
        _mock_meta(invocation_id="i1"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "insufficient context"
    assert router.complete.call_count == 1


async def test_executor_calls_triage_on_schema_failure_then_escalates():
    yaml_backbone = """
capability_name: x
version: 1
steps:
  - name: step1
    kind: llm
    prompt: p.md
    output_schema: s.json
final_output: "{{ state.step1 }}"
"""
    version = _multistep_version(
        yaml_backbone,
        step_content={"step1": "prompt"},
        output_schemas={"step1": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"not_title": "x"},
        _mock_meta(invocation_id="i1"),
    )

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.ESCALATE_TO_CLAUDE,
        rationale="output shape is structurally broken",
    )

    executor = SkillExecutor(router, triage=triage)
    result = await executor.execute(
        skill=_make_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "escalated"
    triage.handle_failure.assert_awaited_once()


async def test_executor_triage_skip_continues():
    yaml_backbone = """
capability_name: x
version: 1
steps:
  - name: step1
    kind: llm
    prompt: p1.md
    output_schema: s1.json
  - name: step2
    kind: llm
    prompt: p2.md
    output_schema: s2.json
final_output: "{{ state.step2 }}"
"""
    version = _multistep_version(
        yaml_backbone,
        step_content={"step1": "p1", "step2": "p2"},
        output_schemas={
            "step1": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            "step2": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"not_title": "x"}, _mock_meta(invocation_id="i1")),  # step1 fails
        ({"ok": True}, _mock_meta(invocation_id="i2")),  # step2 succeeds
    ]

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.SKIP_STEP, rationale="step1 non-essential",
    )

    executor = SkillExecutor(router, triage=triage)
    result = await executor.execute(
        skill=_make_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["step1"] == {}
    assert result.state["step2"] == {"ok": True}


async def test_executor_with_repository_writes_run_and_steps():
    """Verifies the executor wires start_run, record_step, finish_run correctly."""
    yaml_backbone = """
capability_name: x
version: 1
steps:
  - name: only
    kind: llm
    prompt: p.md
    output_schema: s.json
final_output: "{{ state.only }}"
"""
    version = _multistep_version(
        yaml_backbone,
        step_content={"only": "prompt"},
        output_schemas={"only": {"type": "object", "properties": {"v": {"type": "integer"}}, "required": ["v"]}},
    )

    router = AsyncMock()
    router.complete.return_value = ({"v": 42}, _mock_meta(invocation_id="i1"))

    # Mock the repository. Track calls.
    repo = AsyncMock()
    repo.start_run.return_value = "run-id-1"

    executor = SkillExecutor(router, run_repository=repo)
    result = await executor.execute(
        skill=_make_skill(), version=version, inputs={"foo": "bar"}, user_id="nick",
    )

    assert result.status == "succeeded"
    repo.start_run.assert_awaited_once()
    repo.record_step.assert_awaited_once()
    repo.finish_run.assert_awaited_once()
    finish_kwargs = repo.finish_run.call_args.kwargs
    assert finish_kwargs["status"] == "succeeded"
    assert finish_kwargs["final_output"] == {"v": 42}
