"""Tests for conditional step execution, on_failure=continue, success tracking,
and output_schema resolution.

Covers Task 9 features:
- Condition field on steps (Jinja expression gating)
- on_failure=continue (absorb errors, set success=False in state)
- success=True injected on successful step outputs
- output_schema: path-based schema resolution (schema key != step name)
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.tool_registry import ToolRegistry


def _make_skill() -> SkillRow:
    return SkillRow(
        id="s1", capability_name="test_cond", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


def _make_version(
    yaml_backbone: str, step_content: dict, output_schemas: dict,
) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None,
        created_at=datetime.now(UTC),
    )


def _mock_meta(**kwargs):
    defaults = {
        "invocation_id": "inv-1", "latency_ms": 100, "tokens_in": 50,
        "tokens_out": 20, "cost_usd": 0.0,
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


# --------------------------------------------------------------------------- #
# Feature 1: Conditional steps
# --------------------------------------------------------------------------- #


async def test_condition_true_runs_step():
    """step_a succeeds with result='ok', step_b has condition checking that
    value — it should run."""
    yaml_backbone = """
capability_name: test_cond
version: 1
steps:
  - name: step_a
    kind: llm
    prompt: steps/step_a.md
    output_schema: schemas/step_a.json
  - name: step_b
    kind: llm
    prompt: steps/step_b.md
    output_schema: schemas/step_b.json
    condition: "state.step_a.result == 'ok'"
final_output: "{{ state.step_b }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "step_a": "Do step A: {{ inputs.text }}",
            "step_b": "Do step B using {{ state.step_a.result }}",
        },
        output_schemas={
            "step_a": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
            "step_b": {
                "type": "object",
                "properties": {"done": {"type": "boolean"}},
                "required": ["done"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"result": "ok"}, _mock_meta(invocation_id="i1")),
        ({"done": True}, _mock_meta(invocation_id="i2")),
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"text": "hello"}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["step_a"]["result"] == "ok"
    assert result.state["step_b"]["done"] is True
    assert router.complete.call_count == 2


async def test_condition_false_skips_step():
    """step_a succeeds with result='ok', step_b has condition checking for
    'fail' — it should be skipped. step_c runs unconditionally."""
    yaml_backbone = """
capability_name: test_cond
version: 1
steps:
  - name: step_a
    kind: llm
    prompt: steps/step_a.md
    output_schema: schemas/step_a.json
  - name: step_b
    kind: llm
    prompt: steps/step_b.md
    output_schema: schemas/step_b.json
    condition: "state.step_a.result == 'fail'"
  - name: step_c
    kind: llm
    prompt: steps/step_c.md
    output_schema: schemas/step_c.json
final_output: "{{ state.step_c }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "step_a": "Do step A",
            "step_b": "Do step B",
            "step_c": "Do step C",
        },
        output_schemas={
            "step_a": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
            "step_b": {"type": "object"},
            "step_c": {
                "type": "object",
                "properties": {"final": {"type": "boolean"}},
                "required": ["final"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"result": "ok"}, _mock_meta(invocation_id="i1")),
        # step_b is skipped — no LLM call
        ({"final": True}, _mock_meta(invocation_id="i2")),
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    # step_b was skipped — present in state with success=False, skipped=True
    assert result.state["step_b"] == {"success": False, "skipped": True}
    assert result.state["step_c"]["final"] is True
    # Only 2 LLM calls (step_a + step_c), step_b skipped
    assert router.complete.call_count == 2


# --------------------------------------------------------------------------- #
# Feature 2: on_failure=continue
# --------------------------------------------------------------------------- #


async def test_on_failure_continue_sets_success_false():
    """A step with on_failure=continue that raises an error should set
    state[step_name] = {success: False, error: ...} and allow the next step
    to run. A subsequent step with condition 'not state.primary.success'
    should run."""
    yaml_backbone = """
capability_name: test_cond
version: 1
steps:
  - name: primary
    kind: llm
    prompt: steps/primary.md
    output_schema: schemas/primary.json
    on_failure: continue
  - name: fallback
    kind: llm
    prompt: steps/fallback.md
    output_schema: schemas/fallback.json
    condition: "not state.primary.success"
final_output: "{{ state.fallback }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "primary": "Do primary",
            "fallback": "Do fallback",
        },
        output_schemas={
            # Schema requires 'value' — the LLM will return wrong shape
            "primary": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            "fallback": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        # primary returns wrong shape → SchemaValidationError
        ({"wrong_key": "x"}, _mock_meta(invocation_id="i1")),
        # fallback runs because condition matches
        ({"ok": True}, _mock_meta(invocation_id="i2")),
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    # primary step should have success=False and error in state
    assert result.state["primary"]["success"] is False
    assert "error" in result.state["primary"]
    # fallback ran because condition evaluated true
    assert result.state["fallback"]["ok"] is True
    assert router.complete.call_count == 2


async def test_on_failure_continue_with_runtime_error():
    """on_failure=continue should also work with general Exception (not just
    typed errors like SchemaValidationError)."""
    yaml_backbone = """
capability_name: test_cond
version: 1
steps:
  - name: flaky
    kind: llm
    prompt: steps/flaky.md
    output_schema: schemas/flaky.json
    on_failure: continue
  - name: final
    kind: llm
    prompt: steps/final.md
    output_schema: schemas/final.json
final_output: "{{ state.final }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "flaky": "Do flaky",
            "final": "Do final",
        },
        output_schemas={
            "flaky": {"type": "object"},
            "final": {
                "type": "object",
                "properties": {"v": {"type": "integer"}},
                "required": ["v"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        RuntimeError("network timeout"),  # flaky step raises
        ({"v": 42}, _mock_meta(invocation_id="i2")),  # final step succeeds
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["flaky"]["success"] is False
    assert "network timeout" in result.state["flaky"]["error"]
    assert result.state["final"]["v"] == 42


# --------------------------------------------------------------------------- #
# Feature 3: Success tracking
# --------------------------------------------------------------------------- #


async def test_successful_llm_step_has_success_true():
    """A successful LLM step should have success=True injected into state."""
    yaml_backbone = """
capability_name: test_cond
version: 1
steps:
  - name: step_a
    kind: llm
    prompt: steps/step_a.md
    output_schema: schemas/step_a.json
final_output: "{{ state.step_a }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={"step_a": "Do step A"},
        output_schemas={
            "step_a": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"value": "hello"}, _mock_meta(invocation_id="i1"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["step_a"]["success"] is True
    assert result.state["step_a"]["value"] == "hello"


async def test_successful_tool_step_has_success_true():
    """A successful tool step should have success=True injected into state."""
    yaml_backbone = """
capability_name: test_cond
version: 1
steps:
  - name: fetch
    kind: tool
    tools: [mock_fetch]
    tool_invocations:
      - tool: mock_fetch
        args:
          url: "https://example.com"
        store_as: page
final_output: "{{ state.fetch }}"
"""
    version = _make_version(yaml_backbone, step_content={}, output_schemas={})

    async def mock_fetch(url: str):
        return {"content": "page data"}

    registry = ToolRegistry()
    registry.register("mock_fetch", mock_fetch)

    executor = SkillExecutor(AsyncMock(), tool_registry=registry)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["fetch"]["success"] is True
    assert result.state["fetch"]["page"] == {"content": "page data"}


# --------------------------------------------------------------------------- #
# Combined: condition + on_failure + success tracking (tier cascade pattern)
# --------------------------------------------------------------------------- #


async def test_tier_cascade_pattern():
    """End-to-end test of the tier cascade pattern used in product watch:
    primary fails with on_failure=continue, fallback runs because
    condition 'not state.primary.success' is true, and the fallback
    result has success=True."""
    yaml_backbone = """
capability_name: product_watch
version: 1
steps:
  - name: primary
    kind: llm
    prompt: steps/primary.md
    output_schema: schemas/primary.json
    on_failure: continue
  - name: fallback
    kind: llm
    prompt: steps/fallback.md
    output_schema: schemas/fallback.json
    condition: "not state.primary.success"
  - name: report
    kind: llm
    prompt: steps/report.md
    output_schema: schemas/report.json
    condition: "state.primary.success or state.get('fallback', {}).get('success', False)"
final_output: "{{ state.report }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "primary": "Primary check",
            "fallback": "Fallback check",
            "report": "Generate report",
        },
        output_schemas={
            "primary": {
                "type": "object",
                "properties": {"price": {"type": "number"}},
                "required": ["price"],
            },
            "fallback": {
                "type": "object",
                "properties": {"price": {"type": "number"}},
                "required": ["price"],
            },
            "report": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        # primary fails schema validation (missing required 'price')
        ({"wrong": "data"}, _mock_meta(invocation_id="i1")),
        # fallback succeeds
        ({"price": 29.99}, _mock_meta(invocation_id="i2")),
        # report runs because fallback.success is True
        ({"summary": "Price is $29.99"}, _mock_meta(invocation_id="i3")),
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    # primary failed gracefully
    assert result.state["primary"]["success"] is False
    # fallback ran and succeeded
    assert result.state["fallback"]["success"] is True
    assert result.state["fallback"]["price"] == 29.99
    # report ran
    assert result.state["report"]["summary"] == "Price is $29.99"
    assert router.complete.call_count == 3


# --------------------------------------------------------------------------- #
# Feature 4: output_schema path-based resolution
# --------------------------------------------------------------------------- #


async def test_output_schema_resolved_by_ref_not_step_name():
    """When a step has output_schema: schemas/foo.json, the executor should
    look up the schema under key 'foo' in output_schemas, not step_name.
    Invalid output should trigger SchemaValidationError + on_failure=continue."""
    yaml_backbone = """
capability_name: test_schema_ref
version: 1
steps:
  - name: extract
    kind: llm
    prompt: steps/extract.md
    output_schema: schemas/product_info.json
    on_failure: continue
  - name: fallback
    kind: llm
    prompt: steps/fallback.md
    output_schema: schemas/product_info.json
    condition: "not state.extract.success"
final_output: "{{ state.fallback }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "extract": "Extract product info",
            "fallback": "Fallback extraction",
        },
        output_schemas={
            "product_info": {
                "type": "object",
                "properties": {
                    "price": {"type": "number"},
                    "title": {"type": "string"},
                },
                "required": ["price", "title"],
            },
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        # extract returns invalid output (missing required 'price' and 'title')
        ({"garbage": True}, _mock_meta(invocation_id="i1")),
        # fallback returns valid output
        ({"price": 29.99, "title": "Test Product"}, _mock_meta(invocation_id="i2")),
    ]

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["extract"]["success"] is False
    assert "error" in result.state["extract"]
    assert result.state["fallback"]["price"] == 29.99
    assert result.state["fallback"]["title"] == "Test Product"
    assert result.state["fallback"]["success"] is True


async def test_prompt_resolved_by_ref_not_step_name():
    """When a step has prompt: steps/foo.md, the executor should look up
    the template under key 'foo' in step_content, not step_name."""
    yaml_backbone = """
capability_name: test_prompt_ref
version: 1
steps:
  - name: my_extract
    kind: llm
    prompt: steps/shared_extractor.md
    output_schema: schemas/extract.json
final_output: "{{ state.my_extract }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={
            "shared_extractor": "Extract from: {{ inputs.text }}",
        },
        output_schemas={
            "extract": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"value": "extracted"}, _mock_meta(invocation_id="i1"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"text": "hello world"}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["my_extract"]["value"] == "extracted"
    # Verify the prompt was rendered with the correct template
    call_kwargs = router.complete.call_args
    assert "hello world" in call_kwargs.kwargs.get("prompt", call_kwargs[1].get("prompt", ""))


async def test_output_schema_ref_validates_correctly():
    """When output_schema resolves to a real schema, valid output should pass
    and invalid output should fail."""
    yaml_backbone = """
capability_name: test_schema_ref
version: 1
steps:
  - name: my_step
    kind: llm
    prompt: steps/my_step.md
    output_schema: schemas/strict_schema.json
final_output: "{{ state.my_step }}"
"""
    version = _make_version(
        yaml_backbone,
        step_content={"my_step": "Do something"},
        output_schemas={
            "strict_schema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"value": 42}, _mock_meta(invocation_id="i1"),
    )

    executor = SkillExecutor(router)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["my_step"]["value"] == 42
    assert result.state["my_step"]["success"] is True
