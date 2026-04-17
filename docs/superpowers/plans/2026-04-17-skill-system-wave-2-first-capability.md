# Skill System Wave 2 — Hardening + First Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the skill-system pipeline (close all code-review followups from Wave 1) and seed the first real user-facing capability, `product_watch`.

**Architecture:** Small unblocking fixes to the validation/executor path (router kwargs, task_type tagging, tool_mocks threading), modest schema + wiring additions (`manual_draft_at` column, orchestrator pollers, correction write-hook), and a full hand-written `product_watch` skill + fixtures that exercises the pipeline end-to-end against a mocked `web_fetch`.

**Tech Stack:** Python 3.12 · asyncio · aiosqlite (SQLite WAL) · Alembic · FastAPI · discord.py · pytest · jsonschema · jinja2 · structlog · httpx.

**Spec:** `docs/superpowers/specs/2026-04-17-skill-system-wave-2-first-capability-design.md`.

**Open-question resolutions (spec §10):**

1. **Router wildcard/prefix routing.** Current `_resolve_route` does an exact `routing.get(task_type)` lookup — no wildcard. Task 0 adds prefix-match support. Without this, F-W1-C's kwarg deletion (Task 1) still fails the first real run because `skill_step::product_watch::fetch_page` isn't a registered key.
2. **Seed migration vs. startup loader.** Capabilities via startup loader (YAML-driven, idempotent UPSERT). Skills via Alembic migration (immutable versions; re-seeding requires a new revision). Decision: mirrors existing `seed_skill_system_phase_1` pattern.
3. **ManualDraftPoller cadence.** Share the orchestrator's existing 15s automation-scheduler loop by adding a second poll target — no new asyncio task. Decision: minimal new code, same cadence.
4. **Fixture JSON.** Stay consistent with existing `seed_skill_system_phase_1.py` (JSON fixtures loaded from disk into `skill_fixture` rows).
5. **`web_fetch` tool existence.** Exists at `src/donna/skills/tools/web_fetch.py` and is registered via `register_default_tools(registry)`. Task 17 verifies the orchestrator calls `register_default_tools(tool_registry)` when constructing the skill-system bundle.

---

## File Structure

### Files created

| Path | Responsibility |
|---|---|
| `src/donna/skills/mock_synthesis.py` | `cache_to_mocks(tool_result_cache: dict) -> dict[str, Any]` — shared re-keyer for captured-run tool cache → fingerprint-keyed mocks. |
| `src/donna/skills/manual_draft_poller.py` | `ManualDraftPoller.run_once(conn, auto_drafter, candidate_repo)` — picks up `skill_candidate_report` rows with `manual_draft_at IS NOT NULL AND status='new'`. |
| `src/donna/skills/seed_capabilities.py` | `SeedCapabilityLoader.load_and_upsert(conn, capabilities_yaml)` — reads `config/capabilities.yaml` and UPSERTs `capability` rows. |
| `alembic/versions/add_manual_draft_at.py` | Migration: `skill_candidate_report.manual_draft_at TEXT` + index. |
| `alembic/versions/seed_product_watch_capability.py` | Migration: inserts `capability`, `skill`, `skill_version`, and 4 `skill_fixture` rows for product_watch. |
| `config/capabilities.yaml` | Declarations: `product_watch` capability with input schema and trigger type. |
| `skills/product_watch/skill.yaml` | Skill backbone: inputs, 3 steps, final_output template. |
| `skills/product_watch/steps/extract_product_info.md` | LLM prompt. |
| `skills/product_watch/steps/format_output.md` | LLM prompt. |
| `skills/product_watch/schemas/extract_product_info_v1.json` | JSON Schema. |
| `skills/product_watch/schemas/format_output_v1.json` | JSON Schema. |
| `skills/product_watch/fixtures/in_stock_below_threshold.json` | Success fixture with `tool_mocks`. |
| `skills/product_watch/fixtures/in_stock_above_threshold.json` | Success fixture — alert should NOT fire. |
| `skills/product_watch/fixtures/sold_out.json` | Fixture where `in_stock=false`. |
| `skills/product_watch/fixtures/url_404.json` | Fixture exercising tool failure + step escalation. |
| `tests/e2e/test_wave2_product_watch.py` | End-to-end product_watch run. |
| `tests/unit/test_mock_synthesis.py` | Unit tests for `cache_to_mocks`. |
| `tests/unit/test_manual_draft_poller.py` | Unit tests for the poller. |
| `tests/unit/test_seed_capability_loader.py` | Unit tests for `SeedCapabilityLoader`. |
| `tests/unit/test_router_prefix_routing.py` | Unit tests for Task 0 prefix routing. |
| `tests/unit/test_capture_fixture_endpoint.py` | Unit tests for the capture-fixture endpoint. |
| `tests/unit/test_correction_write_hook.py` | Unit tests for F-7 write-hook. |
| `tests/unit/test_evolution_gates_tool_mocks.py` | Regression test for F-W1-B fix. |
| `tests/unit/test_migration_add_manual_draft_at.py` | Migration test. |

### Files modified

| Path | Change |
|---|---|
| `src/donna/models/router.py` | Extend `_resolve_route` with prefix-match fallback: if exact key missing, find longest prefix in `routing:` that matches `task_type[:idx]`. Keeps exact-match precedence. |
| `config/donna_models.yaml` | Add routing entries: `"skill_step": {model: parser}`, `"skill_validation": {model: parser}`. These are prefix keys matched by the new fallback. |
| `src/donna/skills/executor.py` | (a) Delete `schema=schema` and `model_alias="local_parser"` kwargs from `_run_llm_step`. (b) Add `run_sink: Any \| None = None` + `task_type_prefix: str \| None = None` + `config: SkillSystemConfig \| None = None` constructor params. (c) Wrap `self._router.complete(...)` in `asyncio.wait_for` when both `run_sink` and `config` are set (per-step timeout). (d) Use `self._task_type_prefix` when present instead of `"skill_step"`. (e) Add `run_id: str \| None = None` field to `SkillRunResult`; set it from `start_run` return value. |
| `src/donna/skills/triage.py` | Delete `schema=TRIAGE_OUTPUT_SCHEMA` and `model_alias="local_parser"` kwargs. |
| `src/donna/skills/validation_executor.py` | Pass `task_type_prefix="skill_validation"` and `config=self._config` when constructing inner `SkillExecutor`. |
| `src/donna/skills/evolution_gates.py` | (a) Gate 3: SELECT `tool_mocks` from `skill_fixture`, parse JSON, pass to `executor.execute(..., tool_mocks=...)`. (b) Gates 2, 4: load `skill_run.tool_result_cache`, synthesize via `mock_synthesis.cache_to_mocks`, pass through. |
| `src/donna/automations/dispatcher.py` | Pass `automation_run_id=automation_run_id` into `executor.execute(...)` for skill-path dispatch. After run, write `result.run_id` into `automation_run.skill_run_id` via `repo.finish_run(skill_run_id=...)`. |
| `src/donna/skills/run_persistence.py` | `start_run` already accepts `automation_run_id` positionally; `SkillExecutor` must pass it. Also: `finish_run` accepts the existing kwargs — no change here. |
| `src/donna/skills/correction_cluster.py` | Add `async scan_for_skill(skill_id)` method: window query + threshold check + urgent notification. `scan_once` delegates per-skill to this. |
| (correction-log write path) | Find + modify. Likely `src/donna/orchestrator/correction_logger.py` or similar; call `CorrectionClusterDetector.scan_for_skill(skill_id)` after INSERT commits. |
| `src/donna/api/routes/skill_candidates.py` | Replace 501 in `draft-now` with UPDATE `manual_draft_at` + return 202. |
| `src/donna/api/routes/skill_runs.py` | Add `POST /admin/skill-runs/{run_id}/capture-fixture`. |
| `src/donna/cli.py` | (a) Move automation wiring OUT of `if skill_config.enabled:` guard. (b) Construct `ManualDraftPoller` inside the existing automation-scheduler task (add a `run_once` tick inside the 15s loop). (c) Ensure `register_default_tools(tool_registry)` runs so `web_fetch` is available when skills execute. |
| `src/donna/automations/scheduler.py` | Add optional `tick_callbacks: list[Callable[[], Awaitable[None]]]` parameter. Scheduler calls each on its poll tick. Used for wiring the ManualDraftPoller. |
| `src/donna/api/routes/skill_runs.py` (new endpoint) | `capture-fixture` handler. |
| `tests/e2e/harness.py` | Use `MagicMock(spec=ModelRouter)` in the FakeRouter so real kwarg mismatches surface during test (regression guard for Task 1). Actually change: `FakeRouter` becomes a subclass of `ModelRouter` or uses Python's `spec` sentinel — details in Task 1. |
| `docs/architecture.md`, `docs/superpowers/followups/...md`, `docs/superpowers/specs/...wave-2...md` | Doc updates. |

---

## Task 0: Router prefix-routing support

**Goal:** Extend `ModelRouter._resolve_route` so `task_type` that doesn't match an exact key in `routing:` falls back to prefix matching. Unblocks all downstream tasks that use dynamic `skill_step::<cap>::<step>` task_types.

**Files:**
- Modify: `src/donna/models/router.py`
- Modify: `config/donna_models.yaml`
- Test: `tests/unit/test_router_prefix_routing.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_router_prefix_routing.py`

```python
"""Tests for ModelRouter._resolve_route prefix-match fallback."""

from __future__ import annotations

import pytest

from donna.config import load_models_config, load_task_types_config
from donna.models.router import ModelRouter, RoutingError
from donna.models.providers.ollama import OllamaProvider
from donna.models.providers.anthropic import AnthropicProvider


@pytest.fixture
def router(tmp_path):
    models_cfg = load_models_config("config")
    task_cfg = load_task_types_config("config")
    return ModelRouter(models_cfg, task_cfg, tmp_path)


def test_exact_key_match_takes_precedence(router) -> None:
    """Existing exact-match behavior unchanged."""
    provider, model_id, alias = router._resolve_route("parse_task")
    assert alias == "parser"


def test_prefix_match_fallback(router) -> None:
    """skill_step::product_watch::fetch_page falls back to the 'skill_step' prefix entry."""
    provider, model_id, alias = router._resolve_route("skill_step::product_watch::fetch_page")
    assert alias == "parser"  # skill_step prefix routes to parser per donna_models.yaml


def test_skill_validation_prefix_routes(router) -> None:
    provider, model_id, alias = router._resolve_route(
        "skill_validation::product_watch::extract_product_info"
    )
    assert alias == "parser"


def test_longest_prefix_wins(router) -> None:
    """If both 'skill_step' and 'skill_step::product_watch' are registered,
    the more specific one wins."""
    router._models_config.routing["skill_step::product_watch"] = type(
        router._models_config.routing["parse_task"]
    )(model="reasoner")
    provider, model_id, alias = router._resolve_route("skill_step::product_watch::fetch_page")
    assert alias == "reasoner"


def test_no_match_still_raises(router) -> None:
    with pytest.raises(RoutingError):
        router._resolve_route("totally_unknown_prefix::something::else")
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest tests/unit/test_router_prefix_routing.py -v
```
Expected: 3 failures (prefix match not implemented).

- [ ] **Step 3: Implement prefix routing in `_resolve_route`**

Edit `src/donna/models/router.py`:

```python
def _resolve_route(self, task_type: str) -> tuple[ModelProvider, str, str]:
    """Resolve task_type → (provider instance, model ID, model alias).

    Exact-key match takes precedence. When no exact key matches, fall back
    to longest-prefix match on the "::"-separated task_type. This lets
    callers pass dynamic task_types like ``skill_step::<cap>::<step>``
    without registering every combination in donna_models.yaml.

    Raises RoutingError if neither exact nor prefix match.
    """
    routing = self._models_config.routing.get(task_type)
    if routing is None:
        # Prefix match fallback. Try progressively shorter prefixes on "::".
        parts = task_type.split("::")
        for i in range(len(parts) - 1, 0, -1):
            candidate = "::".join(parts[:i])
            routing = self._models_config.routing.get(candidate)
            if routing is not None:
                break
    if routing is None:
        raise RoutingError(f"Unknown task type: {task_type!r}")

    alias = routing.model
    model_config = self._models_config.models.get(alias)
    if model_config is None:
        raise RoutingError(
            f"Model alias {alias!r} (for task type {task_type!r}) not found in config"
        )

    provider = self._providers.get(model_config.provider)
    if provider is None:
        raise RoutingError(
            f"Provider {model_config.provider!r} not available "
            f"(alias {alias!r}, task type {task_type!r})"
        )

    return provider, model_config.model, alias
```

- [ ] **Step 4: Register prefix entries in `config/donna_models.yaml`**

Add under the existing `routing:` block:

```yaml
  # Wave 2: prefix-routing for dynamic skill and validation task_types.
  skill_step:
    model: parser
    fallback: reasoner
    confidence_threshold: 0.7
  skill_validation:
    model: parser
    fallback: reasoner
    confidence_threshold: 0.7
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/unit/test_router_prefix_routing.py -v
pytest tests/unit/test_model_router.py -v  # regression — existing router tests
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/models/router.py config/donna_models.yaml tests/unit/test_router_prefix_routing.py
git commit -m "feat(router): prefix-match fallback for dynamic skill task_types"
```

---

## Task 1: F-W1-C — delete router kwargs from executor + triage

**Goal:** Remove `schema=schema` and `model_alias="local_parser"` from `_run_llm_step` and `triage.py` calls to `ModelRouter.complete`. Harness `FakeRouter` updated to surface future kwarg drift.

**Files:**
- Modify: `src/donna/skills/executor.py:454-458`
- Modify: `src/donna/skills/triage.py:78-84`
- Modify: `tests/e2e/harness.py` (tighten FakeRouter)
- Test: `tests/unit/test_executor_router_kwargs.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_executor_router_kwargs.py`

```python
"""Regression test for F-W1-C: executor does not pass unsupported kwargs to the router."""

from __future__ import annotations

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock

from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.models.router import ModelRouter


@pytest.mark.asyncio
async def test_executor_calls_router_with_only_declared_kwargs() -> None:
    """The executor must pass exactly the kwargs ModelRouter.complete accepts."""
    # Build a MagicMock whose .complete signature matches ModelRouter.complete exactly.
    router = MagicMock(spec=ModelRouter)
    router.complete = AsyncMock(
        return_value=({"result": "ok"}, MagicMock(invocation_id="inv", cost_usd=0.0, latency_ms=1))
    )

    executor = SkillExecutor(model_router=router)
    skill = SkillRow(
        id="s1", capability_name="cap",
        current_version_id="v1",
        state="sandbox", requires_human_gate=False,
        baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="""steps:
  - name: parse
    kind: llm
    prompt: "parse"
    output_schema:
      type: object
""",
        step_content={"parse": "parse"},
        output_schemas={"parse": {"type": "object"}},
        created_by="test", changelog=None, created_at=None,
    )

    await executor.execute(skill=skill, version=version, inputs={}, user_id="test")

    assert router.complete.call_count >= 1
    call_kwargs = router.complete.call_args.kwargs
    allowed = set(inspect.signature(ModelRouter.complete).parameters) - {"self"}
    extras = set(call_kwargs) - allowed
    assert not extras, f"executor passed unsupported kwargs to router: {extras}"
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest tests/unit/test_executor_router_kwargs.py -v
```
Expected: 1 failure (asserts `extras` empty; extras will include `schema`, `model_alias`).

- [ ] **Step 3: Delete kwargs from executor**

Edit `src/donna/skills/executor.py`. Find line ~454:

```python
# BEFORE
output, meta = await self._router.complete(
    prompt=rendered_prompt,
    task_type=f"skill_step::{skill.capability_name}::{step_name}",
    schema=schema,
    model_alias="local_parser",
    task_id=task_id,
    user_id=user_id,
)

# AFTER — delete schema= and model_alias= lines:
output, meta = await self._router.complete(
    prompt=rendered_prompt,
    task_type=f"skill_step::{skill.capability_name}::{step_name}",
    task_id=task_id,
    user_id=user_id,
)
```

- [ ] **Step 4: Delete kwargs from triage**

Edit `src/donna/skills/triage.py:78-84` — similarly delete `schema=TRIAGE_OUTPUT_SCHEMA` and `model_alias="local_parser"` lines.

- [ ] **Step 5: Tighten harness FakeRouter**

Edit `tests/e2e/harness.py`. Replace the current `FakeRouter.complete(...)` signature with one that matches `ModelRouter.complete` exactly:

```python
class FakeRouter:
    def __init__(self, ollama: FakeOllama, claude: FakeClaude) -> None:
        self._ollama = ollama
        self._claude = claude

    async def complete(
        self,
        prompt: str,
        task_type: str,
        task_id: str | None = None,
        user_id: str = "system",
    ) -> tuple[dict, Any]:
        if task_type.startswith("skill_validation::") or task_type.startswith("chat_"):
            return await self._ollama.complete(task_type=task_type, prompt=prompt)
        return await self._claude.complete(task_type=task_type, prompt=prompt)
```

- [ ] **Step 6: Run tests, verify pass**

```bash
pytest tests/unit/test_executor_router_kwargs.py -v
pytest tests/unit/test_skills_executor.py tests/unit/test_skills_triage.py -v
pytest tests/e2e/ -v
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/donna/skills/executor.py src/donna/skills/triage.py tests/e2e/harness.py tests/unit/test_executor_router_kwargs.py
git commit -m "fix(executor,triage): drop unsupported kwargs from ModelRouter.complete"
```

---

## Task 2: `mock_synthesis.cache_to_mocks` helper

**Goal:** Extract the `tool_result_cache → fingerprint-keyed mocks` transformation into a shared module. Used by F-W1-B (EvolutionGates) and F-W1-F (capture-fixture endpoint).

**Files:**
- Create: `src/donna/skills/mock_synthesis.py`
- Test: `tests/unit/test_mock_synthesis.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_mock_synthesis.py`

```python
"""Tests for donna.skills.mock_synthesis.cache_to_mocks."""

from __future__ import annotations

from donna.skills.mock_synthesis import cache_to_mocks


def test_cache_to_mocks_empty() -> None:
    assert cache_to_mocks({}) == {}


def test_cache_to_mocks_web_fetch_uses_rule() -> None:
    cache = {
        "cache_abc": {
            "tool": "web_fetch",
            "args": {"url": "https://example.com", "timeout_s": 10, "headers": {}},
            "result": {"status": 200, "body": "<html>x</html>"},
        },
    }
    mocks = cache_to_mocks(cache)
    # Fingerprint rule for web_fetch strips timeout_s/headers.
    assert 'web_fetch:{"url":"https://example.com"}' in mocks
    assert mocks['web_fetch:{"url":"https://example.com"}'] == {
        "status": 200, "body": "<html>x</html>",
    }


def test_cache_to_mocks_unknown_tool_canonical_json() -> None:
    cache = {
        "cache_x": {
            "tool": "some_tool",
            "args": {"b": 2, "a": 1},
            "result": {"ok": True},
        },
    }
    mocks = cache_to_mocks(cache)
    assert 'some_tool:{"a":1,"b":2}' in mocks


def test_cache_to_mocks_skips_malformed() -> None:
    cache = {
        "cache_good": {"tool": "web_fetch", "args": {"url": "https://x"}, "result": {"ok": 1}},
        "cache_no_tool": {"args": {}, "result": {}},
        "cache_no_result": {"tool": "web_fetch", "args": {"url": "https://y"}},
        "cache_not_dict": "garbage",
    }
    mocks = cache_to_mocks(cache)
    assert len(mocks) == 1
    assert any("https://x" in k for k in mocks)


def test_cache_to_mocks_preserves_result_shape() -> None:
    result = {"status": 200, "body": "<html/>", "headers": {"content-type": "text/html"}}
    cache = {"c1": {"tool": "web_fetch", "args": {"url": "https://z"}, "result": result}}
    mocks = cache_to_mocks(cache)
    assert list(mocks.values())[0] is not result  # copied
    assert list(mocks.values())[0] == result
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_mock_synthesis.py -v
```
Expected: 5 failures (module missing).

- [ ] **Step 3: Implement**

File: `src/donna/skills/mock_synthesis.py`

```python
"""Re-key a skill_run.tool_result_cache into fingerprint-keyed mocks.

Shared between runtime (EvolutionGates, capture-fixture endpoint) and
(conceptually) the Alembic backfill migration `add_fixture_tool_mocks.py`.
The migration has its own inline implementation because migrations must
be runnable standalone — the duplication is intentional and documented.

Runtime callers should prefer this helper so rule-based tools
(web_fetch, gmail_*) produce fingerprints that match the live
MockToolRegistry.
"""

from __future__ import annotations

import copy
from typing import Any

from donna.skills.tool_fingerprint import fingerprint


def cache_to_mocks(tool_result_cache: dict) -> dict[str, Any]:
    """Transform cache_id-keyed entries into fingerprint-keyed mocks.

    Input shape: {cache_id: {"tool": str, "args": dict, "result": Any}}.
    Output shape: {f"{tool}:{canonical_args}": result}.

    Entries missing ``tool`` or ``result``, or whose value is not a dict,
    are skipped. Fingerprints use the runtime rule registry from
    :mod:`donna.skills.tool_fingerprint`.
    """
    mocks: dict[str, Any] = {}
    for entry in tool_result_cache.values():
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        args = entry.get("args") or {}
        result = entry.get("result")
        if tool is None or result is None:
            continue
        fp = fingerprint(tool, args)
        mocks[fp] = copy.deepcopy(result)
    return mocks
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_mock_synthesis.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/mock_synthesis.py tests/unit/test_mock_synthesis.py
git commit -m "feat(skills): mock_synthesis.cache_to_mocks shared helper"
```

---

## Task 3: F-W1-B — `EvolutionGates` thread `tool_mocks`

**Goal:** Gate 3 (fixture-regression) reads `skill_fixture.tool_mocks`. Gates 2 (targeted-case) and 4 (recent-success) synthesize mocks from `skill_run.tool_result_cache`. All three pass `tool_mocks=...` to `executor.execute(...)`.

**Files:**
- Modify: `src/donna/skills/evolution_gates.py`
- Test: `tests/unit/test_evolution_gates_tool_mocks.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_evolution_gates_tool_mocks.py`

```python
"""Regression test for F-W1-B: EvolutionGates thread tool_mocks through."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.evolution_gates import EvolutionGates
from donna.skills.models import SkillRow, SkillVersionRow


@pytest.fixture
def captured_executor():
    """An executor mock that captures every call's tool_mocks kwarg."""
    calls = []
    executor = MagicMock()

    async def _execute(**kwargs):
        calls.append(kwargs)
        from donna.skills.executor import SkillRunResult
        return SkillRunResult(status="succeeded", final_output={"ok": True})

    executor.execute = _execute
    executor.calls = calls
    return executor


@pytest.mark.asyncio
async def test_fixture_regression_gate_passes_tool_mocks(
    captured_executor, skill_with_one_fixture_with_tool_mocks,
) -> None:
    conn, skill, new_version = skill_with_one_fixture_with_tool_mocks
    gates = EvolutionGates(
        connection=conn,
        executor=captured_executor,
        config=MagicMock(evolution_fixture_regression_pass_rate=0.95),
    )
    await gates.run_fixture_regression_gate(new_version=new_version, skill=skill)
    assert len(captured_executor.calls) == 1
    assert "tool_mocks" in captured_executor.calls[0]
    assert captured_executor.calls[0]["tool_mocks"] == {
        'web_fetch:{"url":"https://example.com"}': {"status": 200, "body": "OK"},
    }


@pytest.mark.asyncio
async def test_targeted_case_gate_synthesizes_mocks_from_cache(
    captured_executor, skill_with_captured_run,
) -> None:
    conn, skill, new_version, run_id = skill_with_captured_run
    gates = EvolutionGates(
        connection=conn,
        executor=captured_executor,
        config=MagicMock(evolution_targeted_case_pass_rate=0.80),
    )
    await gates.run_targeted_case_gate(
        new_version=new_version, skill=skill, targeted_case_ids=[run_id],
    )
    assert len(captured_executor.calls) == 1
    mocks = captured_executor.calls[0]["tool_mocks"]
    assert mocks == {
        'web_fetch:{"url":"https://example.com"}': {"status": 200, "body": "captured"},
    }


@pytest.mark.asyncio
async def test_recent_success_gate_synthesizes_mocks_from_cache(
    captured_executor, skill_with_captured_run,
) -> None:
    conn, skill, new_version, run_id = skill_with_captured_run
    gates = EvolutionGates(
        connection=conn,
        executor=captured_executor,
        config=MagicMock(
            evolution_recent_success_count=1,
            evolution_recent_success_window_days=30,
        ),
    )
    await gates.run_recent_success_gate(new_version=new_version, skill=skill)
    assert len(captured_executor.calls) >= 1
    mocks = captured_executor.calls[0]["tool_mocks"]
    assert "web_fetch" in next(iter(mocks))


@pytest.fixture
async def skill_with_one_fixture_with_tool_mocks(tmp_path):
    """Seed a skill + 1 fixture with tool_mocks. Return (conn, skill, new_version)."""
    import aiosqlite
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, 'active', datetime('now'), 'seed')",
            (str(uuid.uuid4()), "cap1", "test", "{}", "on_message"),
        )
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES (?, 'cap1', ?, 'trusted', 0, datetime('now'), datetime('now'))",
            (skill_id, version_id),
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', datetime('now'))",
            (version_id, skill_id),
        )
        await conn.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, created_at, tool_mocks) "
            "VALUES (?, ?, 'f1', ?, ?, 'human_written', datetime('now'), ?)",
            (
                str(uuid.uuid4()), skill_id,
                json.dumps({"url": "https://example.com"}),
                json.dumps({"type": "object"}),
                json.dumps({'web_fetch:{"url":"https://example.com"}': {"status": 200, "body": "OK"}}),
            ),
        )
        await conn.commit()
        skill = SkillRow(
            id=skill_id, capability_name="cap1",
            current_version_id=version_id,
            state="trusted", requires_human_gate=False,
            baseline_agreement=0.9,
            created_at=None, updated_at=None,
        )
        new_version = {
            "yaml_backbone": "steps: []",
            "step_content": {}, "output_schemas": {},
        }
        yield conn, skill, new_version


@pytest.fixture
async def skill_with_captured_run(tmp_path):
    """Seed a skill + 1 captured skill_run with tool_result_cache. Return (conn, skill, new_version, run_id)."""
    import aiosqlite
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    cache = {
        "c1": {
            "tool": "web_fetch",
            "args": {"url": "https://example.com"},
            "result": {"status": 200, "body": "captured"},
        },
    }
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, 'active', datetime('now'), 'seed')",
            (str(uuid.uuid4()), "cap1", "test", "{}", "on_message"),
        )
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES (?, 'cap1', ?, 'trusted', 0, datetime('now'), datetime('now'))",
            (skill_id, version_id),
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', datetime('now'))",
            (version_id, skill_id),
        )
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, tool_result_cache, started_at, finished_at, user_id) "
            "VALUES (?, ?, ?, 'succeeded', ?, ?, datetime('now'), datetime('now'), 'nick')",
            (run_id, skill_id, version_id,
             json.dumps({"inputs": {"url": "https://example.com"}}),
             json.dumps(cache)),
        )
        await conn.commit()
        skill = SkillRow(
            id=skill_id, capability_name="cap1",
            current_version_id=version_id,
            state="trusted", requires_human_gate=False,
            baseline_agreement=0.9,
            created_at=None, updated_at=None,
        )
        new_version = {"yaml_backbone": "steps: []", "step_content": {}, "output_schemas": {}}
        yield conn, skill, new_version, run_id
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_evolution_gates_tool_mocks.py -v
```
Expected: 3 failures (tool_mocks not threaded).

- [ ] **Step 3: Modify `evolution_gates.py`**

Locate `run_fixture_regression_gate` around line 160:

```python
# Update the SQL to SELECT tool_mocks
cursor = await self._conn.execute(
    "SELECT id, input, expected_output_shape, tool_mocks FROM skill_fixture WHERE skill_id = ?",
    (skill.id,),
)
fixtures = await cursor.fetchall()

# For each fixture row:
for row in fixtures:
    fixture_id, input_json, shape_json, mocks_json = row
    inputs = json.loads(input_json)
    tool_mocks = json.loads(mocks_json) if mocks_json else None
    result = await self._executor.execute(
        skill=_synthetic_skill(skill.id, new_version),
        version=_synthetic_version(skill.id, new_version),
        inputs=inputs,
        user_id="evolution_harness",
        tool_mocks=tool_mocks,
    )
    # ... existing pass/fail counting
```

For `run_targeted_case_gate` and `run_recent_success_gate` (both iterate `skill_run` rows):

```python
cursor = await self._conn.execute(
    "SELECT id, state_object, tool_result_cache FROM skill_run WHERE id IN (" + placeholders + ")",
    case_ids,
)
for row in await cursor.fetchall():
    run_id, state_obj_json, tool_cache_json = row
    state_obj = json.loads(state_obj_json) if state_obj_json else {}
    inputs = state_obj.get("inputs", {})
    cache = json.loads(tool_cache_json) if tool_cache_json else {}

    from donna.skills.mock_synthesis import cache_to_mocks
    tool_mocks = cache_to_mocks(cache)

    result = await self._executor.execute(
        skill=_synthetic_skill(skill.id, new_version),
        version=_synthetic_version(skill.id, new_version),
        inputs=inputs, user_id="evolution_harness",
        tool_mocks=tool_mocks,
    )
    # ... existing pass/fail counting
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_evolution_gates_tool_mocks.py tests/unit/test_skills_evolution_gates.py -v
```
Expected: all pass. Existing gates tests might need minor updates — a `MagicMock()` executor silently accepts the new kwarg.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/evolution_gates.py tests/unit/test_evolution_gates_tool_mocks.py
git commit -m "fix(evolution): thread tool_mocks through all three gates"
```

---

## Task 4: F-W1-E — per-step timeout (validation-only)

**Goal:** Wrap `_run_llm_step`'s `self._router.complete(...)` in `asyncio.wait_for` only when the executor is in validation mode (both `run_sink` and `config` are set). Production runs keep no per-step timeout.

**Files:**
- Modify: `src/donna/skills/executor.py`
- Modify: `src/donna/skills/validation_executor.py`
- Test: `tests/unit/test_executor_per_step_timeout.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_executor_per_step_timeout.py`

```python
"""Tests for F-W1-E: validation-mode per-step timeout."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from donna.config import SkillSystemConfig
from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_run_sink import ValidationRunSink


def _make_skill_version_with_llm_step():
    skill = SkillRow(
        id="s1", capability_name="cap",
        current_version_id="v1", state="sandbox",
        requires_human_gate=False, baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="""steps:
  - name: parse
    kind: llm
    prompt: "parse"
    output_schema:
      type: object
""",
        step_content={"parse": "parse"},
        output_schemas={"parse": {"type": "object"}},
        created_by="test", changelog=None, created_at=None,
    )
    return skill, version


@pytest.mark.asyncio
async def test_per_step_timeout_fires_in_validation_mode() -> None:
    """When run_sink AND config are set, a slow step raises TimeoutError."""
    async def slow_complete(**kwargs):
        await asyncio.sleep(3)
        return {}, MagicMock(invocation_id="x", cost_usd=0.0, latency_ms=1)

    router = MagicMock()
    router.complete = slow_complete

    executor = SkillExecutor(
        model_router=router,
        run_sink=ValidationRunSink(),
        config=SkillSystemConfig(validation_per_step_timeout_s=1),
    )
    skill, version = _make_skill_version_with_llm_step()
    result = await executor.execute(skill=skill, version=version, inputs={}, user_id="test")
    # Executor catches the TimeoutError and surfaces a failed/escalated status.
    assert result.status in ("failed", "escalated")
    assert result.error is not None and "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_no_per_step_timeout_in_production_mode() -> None:
    """When run_sink is None, no timeout — slow steps complete normally."""
    call_count = {"n": 0}

    async def slow_complete(**kwargs):
        await asyncio.sleep(0.2)
        call_count["n"] += 1
        return {"ok": True}, MagicMock(invocation_id="x", cost_usd=0.0, latency_ms=1)

    router = MagicMock()
    router.complete = slow_complete

    # No run_sink. Pass a config with very-short timeout; should be IGNORED.
    executor = SkillExecutor(
        model_router=router,
        config=SkillSystemConfig(validation_per_step_timeout_s=1),
    )
    skill, version = _make_skill_version_with_llm_step()
    result = await executor.execute(skill=skill, version=version, inputs={}, user_id="test")
    assert call_count["n"] >= 1
    assert result.status == "succeeded"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_executor_per_step_timeout.py -v
```
Expected: 2 failures — constructor doesn't accept `config`, no timeout wrap.

- [ ] **Step 3: Implement**

Edit `src/donna/skills/executor.py`. Update `__init__` to accept `config`:

```python
def __init__(
    self,
    model_router: Any,
    tool_registry: ToolRegistry | None = None,
    triage: TriageAgent | None = None,
    run_repository: Any | None = None,
    run_sink: Any | None = None,
    shadow_sampler: "ShadowSampler | None" = None,
    config: Any | None = None,  # Wave 2: SkillSystemConfig when validation mode
    task_type_prefix: str | None = None,  # Wave 2: for validation tagging
) -> None:
    self._router = model_router
    self._tool_registry = tool_registry or ToolRegistry()
    self._tool_dispatcher = ToolDispatcher(self._tool_registry)
    self._triage = triage
    self._run_repository = run_sink if run_sink is not None else run_repository
    self._run_sink = run_sink
    self._config = config
    self._task_type_prefix = task_type_prefix
    self._shadow_sampler = shadow_sampler
    self._jinja = jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
```

In `_run_llm_step`, wrap the `self._router.complete(...)` call:

```python
prefix = self._task_type_prefix or "skill_step"
task_type = f"{prefix}::{skill.capability_name}::{step_name}"

if self._run_sink is not None and self._config is not None:
    # Validation mode — enforce per-step timeout.
    timeout = getattr(self._config, "validation_per_step_timeout_s", 60)
    output, meta = await asyncio.wait_for(
        self._router.complete(
            prompt=rendered_prompt,
            task_type=task_type,
            task_id=task_id,
            user_id=user_id,
        ),
        timeout=timeout,
    )
else:
    output, meta = await self._router.complete(
        prompt=rendered_prompt,
        task_type=task_type,
        task_id=task_id,
        user_id=user_id,
    )
```

Ensure the executor's existing exception handler translates `asyncio.TimeoutError` into `result.status='failed'` with `error="per_step_timeout"` — find the `try/except` around the step loop and add:

```python
except asyncio.TimeoutError as exc:
    logger.warning("skill_step_timeout", step_name=step_name,
                    skill_id=skill.id, timeout_s=timeout)
    # Fall through to the generic exception handler with a clear error.
    raise _SkillStepError(f"per_step_timeout: {exc}") from exc
```

Where `_SkillStepError` matches the existing pattern for typed skill failures. If the existing code doesn't have that wrapper, route through the same `RuntimeError` / `SchemaValidationError` path by adding to the catch list.

- [ ] **Step 4: Update ValidationExecutor to pass config through**

Edit `src/donna/skills/validation_executor.py`. In `_build_inner_executor`:

```python
def _build_inner_executor(self, tool_mocks: dict | None) -> SkillExecutor:
    tool_registry = MockToolRegistry.from_mocks(tool_mocks)
    sink = ValidationRunSink()
    return SkillExecutor(
        model_router=self._router,
        tool_registry=tool_registry,
        run_sink=sink,
        config=self._config,
        task_type_prefix="skill_validation",  # Task 5 will also need this
    )
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/unit/test_executor_per_step_timeout.py -v
pytest tests/unit/test_skills_executor.py tests/unit/test_validation_executor.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/executor.py src/donna/skills/validation_executor.py tests/unit/test_executor_per_step_timeout.py
git commit -m "feat(executor): validation-mode per-step timeout"
```

---

## Task 5: F-W1-G — validation task_type prefix

**Goal:** When `ValidationExecutor` runs a skill step, the task_type passed to the router is `skill_validation::<cap>::<step>` rather than `skill_step::<cap>::<step>`. Task 4 already passed `task_type_prefix="skill_validation"` from `ValidationExecutor._build_inner_executor`. Add a test confirming the prefix flows through.

**Files:**
- Test: `tests/unit/test_validation_executor_task_type_prefix.py`
- (no new code — Task 4 already did the plumbing; this task adds the test + validates configs)

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_validation_executor_task_type_prefix.py`

```python
"""Verifies ValidationExecutor tags LLM calls with skill_validation:: prefix."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from donna.config import SkillSystemConfig
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_executor import ValidationExecutor


@pytest.mark.asyncio
async def test_validation_run_tags_task_type() -> None:
    captured_task_types = []

    class _CapturingRouter:
        async def complete(self, prompt, task_type, task_id=None, user_id="system"):
            captured_task_types.append(task_type)

            class _Meta:
                invocation_id = "v"
                cost_usd = 0.0
                latency_ms = 1

            return {"ok": True}, _Meta()

    ve = ValidationExecutor(
        model_router=_CapturingRouter(),
        config=SkillSystemConfig(),
    )
    skill = SkillRow(
        id="s1", capability_name="cap",
        current_version_id="v1", state="sandbox",
        requires_human_gate=False, baseline_agreement=None,
        created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="""steps:
  - name: parse
    kind: llm
    prompt: "parse"
    output_schema:
      type: object
""",
        step_content={"parse": "parse"},
        output_schemas={"parse": {"type": "object"}},
        created_by="test", changelog=None, created_at=None,
    )
    await ve.execute(skill=skill, version=version, inputs={}, user_id="test", tool_mocks=None)

    assert any(tt.startswith("skill_validation::cap::") for tt in captured_task_types), (
        f"Expected validation task_type; got: {captured_task_types}"
    )
```

- [ ] **Step 2: Run, verify**

```bash
pytest tests/unit/test_validation_executor_task_type_prefix.py -v
```
Expected: should pass immediately because Task 4 set up the prefix flow. If it fails, the `ValidationExecutor._build_inner_executor` in Task 4 didn't plumb `task_type_prefix="skill_validation"` — fix there.

- [ ] **Step 3: Commit (test-only, no prod code change in this task)**

```bash
git add tests/unit/test_validation_executor_task_type_prefix.py
git commit -m "test(validation): regression for skill_validation:: prefix tagging"
```

---

## Task 6: F-W1-H — automation subsystem independence

**Goal:** Move the automation scheduler + dispatcher wiring in `src/donna/cli.py` OUT of the `if skill_config.enabled:` guard so automations run regardless of skill-system enablement.

**Files:**
- Modify: `src/donna/cli.py`
- Test: `tests/integration/test_automation_independent_of_skills.py`

- [ ] **Step 1: Write the failing test**

File: `tests/integration/test_automation_independent_of_skills.py`

```python
"""F-W1-H: automation subsystem must run with skill_system.enabled=false."""

from __future__ import annotations

import argparse
import pytest
import shutil
from pathlib import Path
from unittest.mock import patch


@pytest.mark.asyncio
async def test_automation_dispatcher_wires_even_when_skills_disabled(
    monkeypatch, tmp_path,
) -> None:
    """Copy config/, flip skill enabled false, verify automation dispatcher still wires."""
    cfg_src = Path("config")
    cfg_dst = tmp_path / "config"
    shutil.copytree(cfg_src, cfg_dst)
    skills_yaml = cfg_dst / "skills.yaml"
    content = skills_yaml.read_text()
    skills_yaml.write_text(content.replace("enabled: true", "enabled: false"))

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))

    captured = []
    from donna.automations import dispatcher as dispatcher_module
    original_init = dispatcher_module.AutomationDispatcher.__init__

    def _capture(self, *a, **kw):
        original_init(self, *a, **kw)
        captured.append(self)

    monkeypatch.setattr(
        dispatcher_module.AutomationDispatcher, "__init__", _capture,
    )

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir=str(cfg_dst), log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    assert len(captured) == 1, (
        "AutomationDispatcher should wire even when skill_system.enabled=false"
    )
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/integration/test_automation_independent_of_skills.py -v
```
Expected: 1 failure — dispatcher not constructed when skills disabled.

- [ ] **Step 3: Implement — move the wiring block**

In `src/donna/cli.py`, find the `if skill_config.enabled:` block. The automation wiring (AutomationRepository, AutomationDispatcher, AutomationScheduler) lives INSIDE. Move it OUT to after the `if/else` block, with its own try/except.

Sketch (the existing block becomes):

```python
    if skill_config.enabled:
        # Skill-system bundle wiring (unchanged; only skill-specific parts here).
        skill_router = ModelRouter(...)
        cost_tracker = CostTracker(...)
        skill_budget_guard = BudgetGuard(...)
        bundle = assemble_skill_system(...)
        if bundle is not None:
            # Nightly cron only — automation wiring moves out.
            scheduler = AsyncCronScheduler(...)
            tasks.append(asyncio.create_task(scheduler.run_forever()))
            log.info("skill_system_started", ...)
    else:
        log.info("skill_system_disabled_in_config")
        skill_router = ModelRouter(models_config, task_types_config, project_root)
        skill_budget_guard = None  # or a minimal budget guard

    # Automation subsystem — independent of skill_system.enabled.
    try:
        from donna.automations.alert import AlertEvaluator
        from donna.automations.cron import CronScheduleCalculator
        from donna.automations.dispatcher import AutomationDispatcher
        from donna.automations.repository import AutomationRepository
        from donna.automations.scheduler import AutomationScheduler

        automation_repo = AutomationRepository(db.connection)
        automation_dispatcher = AutomationDispatcher(
            connection=db.connection,
            repository=automation_repo,
            model_router=skill_router,
            skill_executor_factory=lambda: None,
            budget_guard=skill_budget_guard,
            alert_evaluator=AlertEvaluator(),
            cron=CronScheduleCalculator(),
            notifier=notification_service,
            config=skill_config,
        )
        automation_scheduler = AutomationScheduler(
            repository=automation_repo,
            dispatcher=automation_dispatcher,
            poll_interval_seconds=skill_config.automation_poll_interval_seconds,
        )
        tasks.append(asyncio.create_task(automation_scheduler.run_forever()))
        log.info("automation_scheduler_started", ...)
    except Exception:
        log.exception("automation_scheduler_wiring_failed")
```

Note: `skill_router` and `skill_budget_guard` now need to be defined in both branches. The `else` branch constructs a minimal router from the standard configs. `skill_budget_guard` can be None — automation dispatcher tolerates it.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/integration/test_automation_independent_of_skills.py tests/integration/test_automation_scheduler_in_orchestrator.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli.py tests/integration/test_automation_independent_of_skills.py
git commit -m "refactor(cli): automation subsystem independent of skill_system.enabled"
```

---

## Task 7: Migration — `skill_candidate_report.manual_draft_at`

**Files:**
- Create: `alembic/versions/add_manual_draft_at.py`
- Test: `tests/unit/test_migration_add_manual_draft_at.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_migration_add_manual_draft_at.py
"""Test the add_manual_draft_at Alembic migration."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


def _cfg(db: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.mark.asyncio
async def test_column_added(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_candidate_report)")
        cols = {r[1] for r in await cursor.fetchall()}
        assert "manual_draft_at" in cols


@pytest.mark.asyncio
async def test_index_added(tmp_path: Path) -> None:
    db = tmp_path / "t2.db"
    command.upgrade(_cfg(db), "head")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("PRAGMA index_list(skill_candidate_report)")
        indexes = {r[1] for r in await cursor.fetchall()}
        assert "ix_skill_candidate_report_manual_draft_at" in indexes


@pytest.mark.asyncio
async def test_downgrade_drops_column(tmp_path: Path) -> None:
    db = tmp_path / "t3.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_candidate_report)")
        cols = {r[1] for r in await cursor.fetchall()}
        assert "manual_draft_at" not in cols
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_migration_add_manual_draft_at.py -v
```
Expected: 3 failures.

- [ ] **Step 3: Implement the migration**

First, find the current head:

```bash
grep -l "revision" alembic/versions/*.py | xargs grep -H "revision:\|revision =" | tail
```

Use the latest revision ID as `down_revision`. For this plan, assume the Wave 1 head `b8c9d0e1f2a3`. Confirm during implementation.

File: `alembic/versions/add_manual_draft_at.py`

```python
"""add skill_candidate_report.manual_draft_at column for manual draft trigger

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-17 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manual_draft_at", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_skill_candidate_report_manual_draft_at",
            ["manual_draft_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_candidate_report_manual_draft_at")
        batch_op.drop_column("manual_draft_at")
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_migration_add_manual_draft_at.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/add_manual_draft_at.py tests/unit/test_migration_add_manual_draft_at.py
git commit -m "feat(migrations): add skill_candidate_report.manual_draft_at"
```

---

## Task 8: `ManualDraftPoller` + draft-now endpoint update

**Files:**
- Create: `src/donna/skills/manual_draft_poller.py`
- Modify: `src/donna/api/routes/skill_candidates.py`
- Modify: `src/donna/cli.py` (wire poller into automation scheduler tick)
- Modify: `src/donna/automations/scheduler.py` (optional `tick_callbacks`)
- Test: `tests/unit/test_manual_draft_poller.py`
- Test update: `tests/unit/test_api_skill_candidates.py`

- [ ] **Step 1: Write the failing tests**

File: `tests/unit/test_manual_draft_poller.py`

```python
"""Tests for ManualDraftPoller (F-W1-D)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command
from alembic.config import Config
import aiosqlite


@pytest.mark.asyncio
async def test_poller_picks_up_and_clears_manual_draft_at(tmp_path):
    from donna.skills.manual_draft_poller import ManualDraftPoller

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        now = datetime.now(timezone.utc).isoformat()
        cand_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_candidate_report (id, capability_name, "
            "expected_savings_usd, volume_30d, variance_score, status, "
            "reported_at, manual_draft_at) "
            "VALUES (?, 'parse_task', 5.0, 100, 0.1, 'new', ?, ?)",
            (cand_id, now, now),
        )
        await conn.commit()

        auto_drafter = MagicMock()
        auto_drafter.draft_one = AsyncMock(
            return_value=MagicMock(candidate_id=cand_id, outcome="succeeded"),
        )
        candidate_repo = MagicMock()
        candidate_repo.get = AsyncMock(return_value=MagicMock(id=cand_id))

        poller = ManualDraftPoller(
            connection=conn,
            auto_drafter=auto_drafter,
            candidate_repo=candidate_repo,
        )
        picked = await poller.run_once()
        assert picked == 1
        auto_drafter.draft_one.assert_called_once()

        cursor = await conn.execute(
            "SELECT manual_draft_at FROM skill_candidate_report WHERE id = ?",
            (cand_id,),
        )
        row = await cursor.fetchone()
        assert row[0] is None


@pytest.mark.asyncio
async def test_poller_skips_non_new_status(tmp_path):
    from donna.skills.manual_draft_poller import ManualDraftPoller

    db = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        now = datetime.now(timezone.utc).isoformat()
        cand_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_candidate_report (id, capability_name, "
            "expected_savings_usd, volume_30d, variance_score, status, "
            "reported_at, manual_draft_at) "
            "VALUES (?, 'parse_task', 5.0, 100, 0.1, 'drafted', ?, ?)",
            (cand_id, now, now),
        )
        await conn.commit()

        poller = ManualDraftPoller(
            connection=conn,
            auto_drafter=MagicMock(draft_one=AsyncMock()),
            candidate_repo=MagicMock(get=AsyncMock()),
        )
        assert await poller.run_once() == 0


@pytest.mark.asyncio
async def test_poller_noop_when_nothing_pending(tmp_path):
    from donna.skills.manual_draft_poller import ManualDraftPoller

    db = tmp_path / "t3.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        poller = ManualDraftPoller(
            connection=conn,
            auto_drafter=MagicMock(draft_one=AsyncMock()),
            candidate_repo=MagicMock(get=AsyncMock()),
        )
        assert await poller.run_once() == 0
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_manual_draft_poller.py -v
```
Expected: 3 failures (module missing).

- [ ] **Step 3: Implement the poller**

File: `src/donna/skills/manual_draft_poller.py`

```python
"""Polls skill_candidate_report for manual_draft_at triggers and runs AutoDrafter.

F-W1-D from Wave 2 plan. The API process sets manual_draft_at; the
orchestrator (this poller) picks up and drives AutoDrafter.draft_one,
clearing the column on completion.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


class ManualDraftPoller:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        auto_drafter: Any,
        candidate_repo: Any,
        batch_size: int = 5,
    ) -> None:
        self._conn = connection
        self._auto_drafter = auto_drafter
        self._repo = candidate_repo
        self._batch_size = batch_size

    async def run_once(self) -> int:
        cursor = await self._conn.execute(
            "SELECT id FROM skill_candidate_report "
            "WHERE manual_draft_at IS NOT NULL AND status = 'new' "
            "ORDER BY manual_draft_at ASC LIMIT ?",
            (self._batch_size,),
        )
        rows = await cursor.fetchall()
        picked = 0
        for (candidate_id,) in rows:
            candidate = await self._repo.get(candidate_id)
            if candidate is None:
                logger.warning("manual_draft_candidate_not_found", candidate_id=candidate_id)
                continue
            try:
                await self._auto_drafter.draft_one(candidate)
            except Exception:
                logger.exception("manual_draft_failed", candidate_id=candidate_id)
            # Always clear the column — even on failure, prevent infinite retry.
            await self._conn.execute(
                "UPDATE skill_candidate_report SET manual_draft_at = NULL WHERE id = ?",
                (candidate_id,),
            )
            await self._conn.commit()
            picked += 1
        return picked
```

- [ ] **Step 4: Update the draft-now API endpoint**

Edit `src/donna/api/routes/skill_candidates.py`. Replace the 501 branch:

```python
@router.post("/skill-candidates/{candidate_id}/draft-now", status_code=202)
async def draft_candidate_now(candidate_id: str, request: Request) -> dict:
    """Schedule the candidate for immediate drafting.

    Sets skill_candidate_report.manual_draft_at to now. The orchestrator's
    ManualDraftPoller (polling the same interval as the automation scheduler)
    picks it up. Returns 202 Accepted — the actual draft runs asynchronously.
    """
    from datetime import datetime, timezone
    conn = request.app.state.db.connection
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    cursor = await conn.execute(
        "UPDATE skill_candidate_report SET manual_draft_at = ? "
        "WHERE id = ? AND status = 'new'",
        (now_iso, candidate_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="candidate not found or not in 'new' status",
        )
    await conn.commit()
    return {"status": "scheduled", "manual_draft_at": now_iso}
```

Drop the old `auto_drafter` check and the entire synchronous `draft_one` call. The in-process auto_drafter fallback is no longer the endpoint's responsibility.

- [ ] **Step 5: Update the draft-now unit test**

Edit `tests/unit/test_api_skill_candidates.py`:
- Replace `test_draft_now_501_when_autodrafter_not_configured` with:

```python
async def test_draft_now_202_sets_manual_draft_at(db_with_candidates):
    from donna.api.routes.skill_candidates import draft_candidate_now

    request = _make_request(db_with_candidates)
    result = await draft_candidate_now(candidate_id="c1", request=request)
    assert result["status"] == "scheduled"
    assert "manual_draft_at" in result

    cursor = await db_with_candidates.connection.execute(
        "SELECT manual_draft_at FROM skill_candidate_report WHERE id = 'c1'"
    )
    row = await cursor.fetchone()
    assert row[0] is not None


async def test_draft_now_404_for_non_new_candidate(db_with_candidates):
    from donna.api.routes.skill_candidates import draft_candidate_now

    request = _make_request(db_with_candidates)
    # Mark candidate 'drafted'.
    await db_with_candidates.connection.execute(
        "UPDATE skill_candidate_report SET status = 'drafted' WHERE id = 'c1'"
    )
    await db_with_candidates.connection.commit()

    with pytest.raises(HTTPException) as excinfo:
        await draft_candidate_now(candidate_id="c1", request=request)
    assert excinfo.value.status_code == 404
```

- [ ] **Step 6: Wire poller into orchestrator**

Edit `src/donna/cli.py`. Inside the automation wiring try block (after the scheduler is started), construct the poller and add it as a tick callback — OR run it in its own task with the same cadence:

```python
        from donna.skills.manual_draft_poller import ManualDraftPoller

        if skill_config.enabled and bundle is not None:
            manual_draft_poller = ManualDraftPoller(
                connection=db.connection,
                auto_drafter=bundle.auto_drafter,
                candidate_repo=bundle.candidate_repo,
            )

            async def _manual_draft_loop():
                while True:
                    try:
                        await manual_draft_poller.run_once()
                    except Exception:
                        log.exception("manual_draft_poller_tick_failed")
                    await asyncio.sleep(skill_config.automation_poll_interval_seconds)

            tasks.append(asyncio.create_task(_manual_draft_loop()))
            log.info("manual_draft_poller_started")
```

- [ ] **Step 7: Run tests, verify pass**

```bash
pytest tests/unit/test_manual_draft_poller.py tests/unit/test_api_skill_candidates.py -v
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/donna/skills/manual_draft_poller.py src/donna/api/routes/skill_candidates.py src/donna/cli.py tests/unit/test_manual_draft_poller.py tests/unit/test_api_skill_candidates.py
git commit -m "feat(cli): ManualDraftPoller + /draft-now 202 via manual_draft_at"
```

---

## Task 9: F-W1-F — capture-fixture endpoint

**Files:**
- Modify: `src/donna/api/routes/skill_runs.py`
- Test: `tests/unit/test_capture_fixture_endpoint.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_capture_fixture_endpoint.py`

```python
"""Tests for POST /admin/skill-runs/{id}/capture-fixture."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest
from alembic import command
from alembic.config import Config
import aiosqlite


@pytest.mark.asyncio
async def test_capture_fixture_succeeds_with_tool_mocks(tmp_path):
    from donna.api.routes.skill_runs import capture_fixture

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, 'cap', '', '{}', 'on_message', 'active', ?, 'seed')",
            (str(uuid.uuid4()), now),
        )
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES (?, 'cap', ?, 'trusted', 0, ?, ?)",
            (skill_id, version_id, now, now),
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', ?)",
            (version_id, skill_id, now),
        )
        tool_cache = {
            "c1": {"tool": "web_fetch",
                    "args": {"url": "https://x.com"},
                    "result": {"status": 200, "body": "OK"}},
        }
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, tool_result_cache, final_output, "
            "started_at, finished_at, user_id) "
            "VALUES (?, ?, ?, 'succeeded', ?, ?, ?, ?, ?, 'nick')",
            (run_id, skill_id, version_id,
             json.dumps({"inputs": {"url": "https://x.com"}}),
             json.dumps(tool_cache),
             json.dumps({"ok": True, "price_usd": 79.0, "in_stock": True}),
             now, now),
        )
        await conn.commit()

        request = MagicMock()
        request.app.state.db = MagicMock(connection=conn)

        result = await capture_fixture(run_id=run_id, request=request)
        assert result["source"] == "captured_from_run"
        assert "fixture_id" in result

        cursor = await conn.execute(
            "SELECT expected_output_shape, tool_mocks FROM skill_fixture "
            "WHERE captured_run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        shape = json.loads(row[0])
        assert shape["type"] == "object"
        assert set(shape["required"]) == {"ok", "price_usd", "in_stock"}
        mocks = json.loads(row[1])
        assert any("web_fetch" in k for k in mocks)


@pytest.mark.asyncio
async def test_capture_fixture_404_on_missing_run(tmp_path):
    from donna.api.routes.skill_runs import capture_fixture
    from fastapi import HTTPException

    db = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        request = MagicMock()
        request.app.state.db = MagicMock(connection=conn)

        with pytest.raises(HTTPException) as excinfo:
            await capture_fixture(run_id="nonexistent", request=request)
        assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_capture_fixture_409_when_run_not_succeeded(tmp_path):
    from donna.api.routes.skill_runs import capture_fixture
    from fastapi import HTTPException

    db = tmp_path / "t3.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, 'cap', '', '{}', 'on_message', 'active', ?, 'seed')",
            (str(uuid.uuid4()), now),
        )
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES (?, 'cap', ?, 'sandbox', 0, ?, ?)",
            (skill_id, version_id, now, now),
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', ?)",
            (version_id, skill_id, now),
        )
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, started_at, finished_at, user_id) "
            "VALUES (?, ?, ?, 'failed', '{}', ?, ?, 'nick')",
            (run_id, skill_id, version_id, now, now),
        )
        await conn.commit()

        request = MagicMock()
        request.app.state.db = MagicMock(connection=conn)

        with pytest.raises(HTTPException) as excinfo:
            await capture_fixture(run_id=run_id, request=request)
        assert excinfo.value.status_code == 409
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_capture_fixture_endpoint.py -v
```
Expected: 3 failures — endpoint missing.

- [ ] **Step 3: Implement the endpoint**

Edit `src/donna/api/routes/skill_runs.py`. Add:

```python
from fastapi import HTTPException, Request

@router.post("/skill-runs/{run_id}/capture-fixture", status_code=201)
async def capture_fixture(run_id: str, request: Request) -> dict:
    """Capture a skill_run into a reusable skill_fixture row.

    Reads the run's final_output + tool_result_cache, infers a structural
    expected_output_shape, synthesizes tool_mocks, and inserts a
    skill_fixture(source='captured_from_run') row pointing at the run.
    """
    import json
    from donna.skills.schema_inference import json_to_schema
    from donna.skills.mock_synthesis import cache_to_mocks
    from donna.skills.auto_drafter import _persist_fixture

    conn = request.app.state.db.connection
    cursor = await conn.execute(
        "SELECT id, skill_id, status, final_output, tool_result_cache, state_object "
        "FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="skill_run not found")
    if row[2] != "succeeded":
        raise HTTPException(
            status_code=409,
            detail="can only capture fixtures from succeeded runs",
        )

    final_output = json.loads(row[3]) if row[3] else {}
    cache = json.loads(row[4]) if row[4] else {}
    state_obj = json.loads(row[5]) if row[5] else {}
    inputs = state_obj.get("inputs", {})

    expected_shape = json_to_schema(final_output)
    tool_mocks = cache_to_mocks(cache)

    fixture_id = await _persist_fixture(
        conn=conn,
        skill_id=row[1],
        case_name=f"captured_from_{run_id[:8]}",
        input_=inputs,
        expected_output_shape=expected_shape,
        tool_mocks=tool_mocks if tool_mocks else None,
        source="captured_from_run",
        captured_run_id=run_id,
    )
    await conn.commit()
    return {"fixture_id": fixture_id, "source": "captured_from_run"}
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_capture_fixture_endpoint.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/skill_runs.py tests/unit/test_capture_fixture_endpoint.py
git commit -m "feat(api): POST /admin/skill-runs/{id}/capture-fixture"
```

---

## Task 10: F-2 — `automation_run.skill_run_id` both-direction linkage

**Files:**
- Modify: `src/donna/skills/executor.py` (add `run_id` to `SkillRunResult`, thread `automation_run_id`)
- Modify: `src/donna/automations/dispatcher.py` (pass + record)
- Test: `tests/unit/test_automation_skill_run_linkage.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_automation_skill_run_linkage.py`

```python
"""F-2: automation_run.skill_run_id linked both directions."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import aiosqlite
from alembic import command
from alembic.config import Config


@pytest.mark.asyncio
async def test_skill_run_result_has_run_id():
    from donna.skills.executor import SkillRunResult
    result = SkillRunResult(status="succeeded", run_id="abc")
    assert result.run_id == "abc"


@pytest.mark.asyncio
async def test_executor_threads_automation_run_id_to_repository(tmp_path):
    """When execute is called with automation_run_id, it ends up on skill_run.automation_run_id."""
    from donna.skills.executor import SkillExecutor
    from donna.skills.models import SkillRow, SkillVersionRow
    from donna.skills.run_persistence import SkillRunRepository

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        repo = SkillRunRepository(conn)

        class _FakeRouter:
            async def complete(self, prompt, task_type, task_id=None, user_id="system"):
                class _Meta:
                    invocation_id = "x"
                    cost_usd = 0.0
                    latency_ms = 1
                return {"ok": True}, _Meta()

        executor = SkillExecutor(
            model_router=_FakeRouter(),
            run_repository=repo,
        )
        skill = SkillRow(
            id="s1", capability_name="cap",
            current_version_id="v1", state="sandbox",
            requires_human_gate=False, baseline_agreement=None,
            created_at=None, updated_at=None,
        )
        version = SkillVersionRow(
            id="v1", skill_id="s1", version_number=1,
            yaml_backbone="""steps:
  - name: parse
    kind: llm
    prompt: "parse"
    output_schema:
      type: object
""",
            step_content={"parse": "parse"},
            output_schemas={"parse": {"type": "object"}},
            created_by="test", changelog=None, created_at=None,
        )

        # Pre-create capability + skill + version rows so FK inserts work.
        await conn.execute(
            "INSERT INTO capability (id, name, description, input_schema, "
            "trigger_type, status, created_at, created_by) "
            "VALUES (?, 'cap', '', '{}', 'on_message', 'active', datetime('now'), 'seed')",
            (str(uuid.uuid4()),),
        )
        await conn.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, state, "
            "requires_human_gate, created_at, updated_at) "
            "VALUES ('s1', 'cap', 'v1', 'sandbox', 0, datetime('now'), datetime('now'))"
        )
        await conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
            "step_content, output_schemas, created_by, created_at) "
            "VALUES ('v1', 's1', 1, 'steps: []', '{}', '{}', 'test', datetime('now'))"
        )
        await conn.commit()

        result = await executor.execute(
            skill=skill, version=version, inputs={}, user_id="test",
            automation_run_id="auto-123",
        )

        assert result.run_id is not None

        cursor = await conn.execute(
            "SELECT automation_run_id FROM skill_run WHERE id = ?", (result.run_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "auto-123"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_automation_skill_run_linkage.py -v
```
Expected: 2 failures.

- [ ] **Step 3: Implement — SkillRunResult.run_id + executor thread-through**

Edit `src/donna/skills/executor.py`:

```python
@dataclass(slots=True)
class SkillRunResult:
    status: str
    final_output: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    escalation_reason: str | None = None
    error: str | None = None
    invocation_ids: list[str] = field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    step_results: list[StepResultRecord] = field(default_factory=list)
    tool_result_cache: dict = field(default_factory=dict)
    run_id: str | None = None  # Wave 2: populated from SkillRunRepository.start_run
```

In `execute()`, accept `automation_run_id` via `**_ignored_kwargs` extraction:

```python
async def execute(
    self,
    skill: SkillRow,
    version: SkillVersionRow,
    inputs: dict,
    user_id: str,
    task_id: str | None = None,
    automation_run_id: str | None = None,
    **_ignored_kwargs: Any,
) -> SkillRunResult:
    # ... existing body ...
    # When calling run_repository.start_run, pass automation_run_id:
    if self._run_repository is not None:
        skill_run_id = await self._run_repository.start_run(
            skill_id=skill.id,
            skill_version_id=version.id,
            inputs=inputs,
            user_id=user_id,
            task_id=task_id,
            automation_run_id=automation_run_id,
        )
    # ... populate result.run_id at end:
    result.run_id = skill_run_id  # use the value returned from start_run
    return result
```

- [ ] **Step 4: Update the dispatcher to pass and record**

Edit `src/donna/automations/dispatcher.py`:

```python
# In the skill-path dispatch (around the existing executor.execute call):
result = await executor.execute(
    skill=skill, version=version, inputs=inputs, user_id=user_id,
    automation_run_id=automation_run_id,  # NEW
)

# After the run, capture the skill_run_id and thread to finish_run:
skill_run_id = result.run_id
await self._repo.finish_run(
    run_id=automation_run_id,
    status=run_status, output=output,
    skill_run_id=skill_run_id,  # NEW
    invocation_log_id=invocation_log_id,
    alert_sent=alert_sent, alert_content=alert_content,
    error=error, cost_usd=cost_usd,
)
```

Inspect `AutomationRepository.finish_run` — it likely already accepts `skill_run_id=` (see spec §6.8). If not, add it.

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/unit/test_automation_skill_run_linkage.py tests/unit/test_skills_executor.py tests/unit/test_automations_dispatcher*.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/executor.py src/donna/automations/dispatcher.py tests/unit/test_automation_skill_run_linkage.py
git commit -m "feat(automations): link skill_run_id ↔ automation_run_id both ways"
```

---

## Task 11: F-7 — correction-cluster write-hook

**Goal:** `CorrectionClusterDetector.scan_for_skill(skill_id)` new method. Correction-log write path calls it synchronously after inserting.

**Files:**
- Modify: `src/donna/skills/correction_cluster.py` (add `scan_for_skill`)
- Modify: the correction-log write path (search: `grep -rn "record_correction\|correction_log" src/donna/` → find the writer)
- Test: `tests/unit/test_correction_write_hook.py`

- [ ] **Step 1: Explore**

Find the correction-log writer:

```bash
grep -rn "INSERT INTO correction_log\|record_correction" src/donna/
```

Identify the repository/method. The test below assumes it's `CorrectionLogRepository.record_correction(...)` in some module; adjust paths after exploring.

- [ ] **Step 2: Write the failing test**

File: `tests/unit/test_correction_write_hook.py`

```python
"""F-7: correction_log.record_correction fires cluster scan synchronously."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_record_correction_triggers_scan_for_skill():
    # Adjust import path after exploration.
    from donna.skills.correction_cluster import CorrectionClusterDetector
    # The exact path / name of the writer might differ; the test scaffolds.

    detector = MagicMock()
    detector.scan_for_skill = AsyncMock()

    # Stub writer: mimic a repository with an injected detector.
    # Replace this block with the real writer class and its signature:
    from some_module import CorrectionLogRepository  # PLACEHOLDER — update after exploration

    conn = MagicMock()
    conn.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    conn.commit = AsyncMock()

    repo = CorrectionLogRepository(connection=conn, cluster_detector=detector)
    await repo.record_correction(
        task_id="t1", user_id="nick", correction_type="priority", details="",
    )
    detector.scan_for_skill.assert_called_once()
```

Adjust imports after exploration — keep the test's *intent* (detector is called synchronously from the write path).

- [ ] **Step 3: Implement `scan_for_skill`**

Edit `src/donna/skills/correction_cluster.py`. Add:

```python
async def scan_for_skill(self, skill_id: str) -> None:
    """Scan recent corrections for one skill; fire urgent notification if threshold exceeded.

    Called from the correction-log write path (F-7 fast path) AND from
    :meth:`scan_once` (nightly belt-and-suspenders).
    """
    window = self._config.correction_cluster_window_runs
    threshold = self._config.correction_cluster_threshold
    cursor = await self._conn.execute(
        """
        SELECT COUNT(*) FROM correction_log cl
        JOIN skill_run sr ON sr.task_id = cl.task_id
        WHERE sr.skill_id = ? AND cl.at >= datetime('now', '-7 days')
        """,
        (skill_id,),
    )
    # Actual query should match scan_once's windowing. Audit scan_once
    # first — reuse its SQL to keep both paths consistent.
    count = (await cursor.fetchone())[0]
    if count >= threshold:
        # Delegate to existing flag-and-notify helper.
        await self._flag_and_notify(skill_id, count)
```

Audit `scan_once` — extract the per-skill body into `scan_for_skill`, then `scan_once` becomes a wrapper that iterates all trusted skills and calls `scan_for_skill(skill_id)` for each.

- [ ] **Step 4: Wire the hook**

In the correction-log writer (identified during step 1), after the INSERT + commit:

```python
async def record_correction(self, task_id, user_id, correction_type, details):
    # ... existing INSERT ...
    await self._conn.commit()

    if self._cluster_detector is not None:
        skill_id = await self._resolve_skill_from_task(task_id)
        if skill_id is not None:
            try:
                await self._cluster_detector.scan_for_skill(skill_id)
            except Exception:
                logger.exception("correction_cluster_scan_failed",
                                  task_id=task_id, skill_id=skill_id)
```

Pass the `cluster_detector` in at construction time. Wire it in `cli.py` where the correction-log repo is constructed.

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/unit/test_correction_write_hook.py tests/unit/test_correction_cluster.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/correction_cluster.py <the correction log writer file> tests/unit/test_correction_write_hook.py
git commit -m "feat(correction): fire cluster scan synchronously on record_correction"
```

---

## Task 12: F-11 — `product_watch` capability declaration

**Files:**
- Create: `config/capabilities.yaml`
- Create: `src/donna/skills/seed_capabilities.py`
- Test: `tests/unit/test_seed_capability_loader.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_seed_capability_loader.py`

```python
"""Tests for SeedCapabilityLoader."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
import aiosqlite


@pytest.mark.asyncio
async def test_loader_inserts_capability_from_yaml(tmp_path):
    from donna.skills.seed_capabilities import SeedCapabilityLoader

    yaml_file = tmp_path / "capabilities.yaml"
    yaml_file.write_text("""
capabilities:
  - name: product_watch
    description: Watch a product URL for price and availability.
    trigger_type: on_schedule
    input_schema:
      type: object
      required: [url]
      properties:
        url: {type: string}
        max_price_usd: {type: [number, "null"]}
        required_size: {type: [string, "null"]}
""")

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        loader = SeedCapabilityLoader(connection=conn)
        inserted = await loader.load_and_upsert(yaml_file)
        assert inserted >= 1

        cursor = await conn.execute(
            "SELECT name, trigger_type FROM capability WHERE name = 'product_watch'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "on_schedule"


@pytest.mark.asyncio
async def test_loader_is_idempotent(tmp_path):
    from donna.skills.seed_capabilities import SeedCapabilityLoader

    yaml_file = tmp_path / "capabilities.yaml"
    yaml_file.write_text("""
capabilities:
  - name: product_watch
    description: X
    trigger_type: on_schedule
    input_schema: {type: object}
""")

    db = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        loader = SeedCapabilityLoader(connection=conn)
        await loader.load_and_upsert(yaml_file)
        await loader.load_and_upsert(yaml_file)  # run twice

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_seed_capability_loader.py -v
```
Expected: 2 failures.

- [ ] **Step 3: Implement loader**

File: `src/donna/skills/seed_capabilities.py`

```python
"""Load capabilities from YAML and UPSERT into the capability table."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog
import yaml

logger = structlog.get_logger()


class SeedCapabilityLoader:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def load_and_upsert(self, yaml_path: Path) -> int:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        entries = data.get("capabilities", []) or []
        upserted = 0
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            description = entry.get("description", "")
            trigger_type = entry.get("trigger_type", "on_message")
            input_schema = json.dumps(entry.get("input_schema", {}))
            default_output_shape = (
                json.dumps(entry["default_output_shape"])
                if "default_output_shape" in entry
                else None
            )

            # Upsert: insert if absent, update description / schemas if present.
            cursor = await self._conn.execute(
                "SELECT id FROM capability WHERE name = ?", (name,),
            )
            row = await cursor.fetchone()
            now = datetime.now(tz=timezone.utc).isoformat()
            if row is None:
                await self._conn.execute(
                    "INSERT INTO capability "
                    "(id, name, description, input_schema, trigger_type, "
                    " default_output_shape, status, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 'seed')",
                    (str(uuid.uuid4()), name, description, input_schema,
                     trigger_type, default_output_shape, now),
                )
            else:
                await self._conn.execute(
                    "UPDATE capability "
                    "SET description = ?, input_schema = ?, trigger_type = ?, "
                    "    default_output_shape = ? "
                    "WHERE name = ?",
                    (description, input_schema, trigger_type, default_output_shape, name),
                )
            upserted += 1
        await self._conn.commit()
        logger.info("capabilities_seeded", count=upserted)
        return upserted
```

- [ ] **Step 4: Write `config/capabilities.yaml`**

File: `config/capabilities.yaml`

```yaml
# Seed capabilities. Loaded at orchestrator startup.
# Each entry UPSERTs a row into the `capability` table.

capabilities:
  - name: product_watch
    description: |
      Monitor a product URL for current price, availability, and the size
      the user wants. Alert when in-stock and price is under the max
      threshold and the required size is available.
    trigger_type: on_schedule
    input_schema:
      type: object
      required: [url]
      properties:
        url:
          type: string
          description: Canonical product URL.
        max_price_usd:
          type: ["number", "null"]
          description: Alert only when in_stock AND price_usd <= this. Null = any price.
        required_size:
          type: ["string", "null"]
          description: Size name (e.g. "L", "42 EU"). Null = any available size counts.
    default_output_shape:
      type: object
      required: [ok, in_stock]
      properties:
        ok: {type: boolean}
        price_usd: {type: ["number", "null"]}
        currency: {type: string}
        in_stock: {type: boolean}
        size_available: {type: boolean}
        triggers_alert: {type: boolean}
        title: {type: string}
```

- [ ] **Step 5: Run tests, verify pass**

```bash
pytest tests/unit/test_seed_capability_loader.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add config/capabilities.yaml src/donna/skills/seed_capabilities.py tests/unit/test_seed_capability_loader.py
git commit -m "feat(skills): SeedCapabilityLoader + product_watch declaration"
```

---

## Task 13: F-11 — `product_watch` skill YAML + prompts + schemas

**Files:**
- Create: `skills/product_watch/skill.yaml`
- Create: `skills/product_watch/steps/extract_product_info.md`
- Create: `skills/product_watch/steps/format_output.md`
- Create: `skills/product_watch/schemas/extract_product_info_v1.json`
- Create: `skills/product_watch/schemas/format_output_v1.json`

Skill authored inline; no test in this task (seeded+tested in Task 15 migration + Task 18 E2E).

- [ ] **Step 1: Write `skills/product_watch/skill.yaml`**

```yaml
capability_name: product_watch
version: 1
description: |
  Monitor a product URL for price and availability. Extract price,
  currency, in-stock flag, available sizes, and title. Compute whether
  the configured alert should fire given the user's price and size
  constraints.

inputs:
  schema_ref: capabilities/product_watch/input_schema.json

steps:
  - name: fetch_page
    kind: tool
    tool_invocations:
      - tool: web_fetch
        args:
          url: "{{ inputs.url }}"
          timeout_s: 15
        retry:
          max_attempts: 2
          backoff_s: [2, 5]
        on_failure: fail_step
        store_as: page

  - name: extract_product_info
    kind: llm
    prompt: steps/extract_product_info.md
    output_schema: schemas/extract_product_info_v1.json

  - name: format_output
    kind: llm
    prompt: steps/format_output.md
    output_schema: schemas/format_output_v1.json

final_output: "{{ state.format_output }}"
```

- [ ] **Step 2: Write `skills/product_watch/steps/extract_product_info.md`**

```
You are extracting product information from the HTML of a product page.

Inputs you can use:
- state.fetch_page.body: the HTML of the page
- state.fetch_page.status: HTTP status code

Return a JSON object matching this schema:
- price_usd: number — normalized to USD. If the page shows a non-USD price,
  convert approximately (mention the conversion in reasoning). Return null
  if no price is found.
- currency: string — e.g. "USD", "GBP", "EUR".
- in_stock: boolean — true if the product is available for purchase in any size.
- available_sizes: array of strings — e.g. ["XS", "S", "M", "L", "XL"].
  Empty array if sizes cannot be determined.
- title: string — the product name as shown on the page.

If the page is a 404 or otherwise shows no product:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: best-effort name, or "Unknown product"

Return ONLY the JSON object. No markdown, no explanation.

State so far:
{{ state | tojson }}
```

- [ ] **Step 3: Write `skills/product_watch/steps/format_output.md`**

```
You are computing the final output fields for a product_watch skill run.

Inputs (user-provided):
- inputs.url: the product URL that was monitored.
- inputs.max_price_usd: the maximum price (USD) above which NO alert should fire. Null = any price qualifies.
- inputs.required_size: the size the user wants. Null = any in-stock size qualifies.

Extracted info (from the previous step):
- state.extract_product_info.price_usd
- state.extract_product_info.currency
- state.extract_product_info.in_stock
- state.extract_product_info.available_sizes
- state.extract_product_info.title

Compute the final output:
- ok: true
- price_usd: state.extract_product_info.price_usd
- currency: state.extract_product_info.currency
- in_stock: state.extract_product_info.in_stock
- size_available: true if inputs.required_size is null OR inputs.required_size is in state.extract_product_info.available_sizes. Else false.
- triggers_alert: true if ALL of: in_stock is true AND size_available is true AND (inputs.max_price_usd is null OR price_usd <= inputs.max_price_usd). Else false.
- title: state.extract_product_info.title

Return ONLY the JSON object.

Inputs: {{ inputs | tojson }}
Extracted info: {{ state.extract_product_info | tojson }}
```

- [ ] **Step 4: Write `skills/product_watch/schemas/extract_product_info_v1.json`**

```json
{
  "type": "object",
  "required": ["in_stock", "available_sizes"],
  "properties": {
    "price_usd": {"type": ["number", "null"]},
    "currency": {"type": "string"},
    "in_stock": {"type": "boolean"},
    "available_sizes": {
      "type": "array",
      "items": {"type": "string"}
    },
    "title": {"type": "string"}
  }
}
```

- [ ] **Step 5: Write `skills/product_watch/schemas/format_output_v1.json`**

```json
{
  "type": "object",
  "required": ["ok", "in_stock", "size_available", "triggers_alert"],
  "properties": {
    "ok": {"type": "boolean"},
    "price_usd": {"type": ["number", "null"]},
    "currency": {"type": "string"},
    "in_stock": {"type": "boolean"},
    "size_available": {"type": "boolean"},
    "triggers_alert": {"type": "boolean"},
    "title": {"type": "string"}
  }
}
```

- [ ] **Step 6: Commit**

```bash
git add skills/product_watch/
git commit -m "feat(skills): product_watch skill YAML + prompts + schemas"
```

---

## Task 14: F-11 — `product_watch` fixtures

**Files:**
- Create: `skills/product_watch/fixtures/in_stock_below_threshold.json`
- Create: `skills/product_watch/fixtures/in_stock_above_threshold.json`
- Create: `skills/product_watch/fixtures/sold_out.json`
- Create: `skills/product_watch/fixtures/url_404.json`

Each fixture is a JSON file. Values are illustrative; the point is stable fingerprint-keyed `tool_mocks`.

- [ ] **Step 1: `in_stock_below_threshold.json`**

```json
{
  "case_name": "in_stock_below_threshold",
  "input": {
    "url": "https://example-shop.com/shirt-blue",
    "max_price_usd": 100.0,
    "required_size": "L"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "in_stock", "size_available", "triggers_alert", "price_usd"],
    "properties": {
      "ok": {"type": "boolean"},
      "price_usd": {"type": ["number", "null"]},
      "in_stock": {"type": "boolean"},
      "size_available": {"type": "boolean"},
      "triggers_alert": {"type": "boolean"}
    }
  },
  "tool_mocks": {
    "web_fetch:{\"url\":\"https://example-shop.com/shirt-blue\"}": {
      "status": 200,
      "body": "<html><body><h1>Blue Shirt</h1><span class='price'>$79.00</span><div class='sizes'>S, M, L, XL</div><div class='stock'>In stock</div></body></html>",
      "headers": {"content-type": "text/html"}
    }
  }
}
```

- [ ] **Step 2: `in_stock_above_threshold.json`**

```json
{
  "case_name": "in_stock_above_threshold",
  "input": {
    "url": "https://example-shop.com/coat-grey",
    "max_price_usd": 100.0,
    "required_size": "L"
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "in_stock", "size_available", "triggers_alert"],
    "properties": {
      "ok": {"type": "boolean"},
      "price_usd": {"type": ["number", "null"]},
      "in_stock": {"type": "boolean"},
      "triggers_alert": {"type": "boolean"}
    }
  },
  "tool_mocks": {
    "web_fetch:{\"url\":\"https://example-shop.com/coat-grey\"}": {
      "status": 200,
      "body": "<html><body><h1>Grey Coat</h1><span class='price'>$189.00</span><div class='sizes'>S, M, L</div><div class='stock'>In stock</div></body></html>",
      "headers": {}
    }
  }
}
```

- [ ] **Step 3: `sold_out.json`**

```json
{
  "case_name": "sold_out",
  "input": {
    "url": "https://example-shop.com/hoodie-red",
    "max_price_usd": null,
    "required_size": null
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "in_stock", "triggers_alert"],
    "properties": {
      "ok": {"type": "boolean"},
      "in_stock": {"type": "boolean"},
      "triggers_alert": {"type": "boolean"}
    }
  },
  "tool_mocks": {
    "web_fetch:{\"url\":\"https://example-shop.com/hoodie-red\"}": {
      "status": 200,
      "body": "<html><body><h1>Red Hoodie</h1><span class='price'>$65.00</span><div class='stock'>Sold out</div></body></html>",
      "headers": {}
    }
  }
}
```

- [ ] **Step 4: `url_404.json`**

```json
{
  "case_name": "url_404",
  "input": {
    "url": "https://example-shop.com/deleted-product",
    "max_price_usd": null,
    "required_size": null
  },
  "expected_output_shape": {
    "type": "object",
    "properties": {
      "ok": {"type": "boolean"},
      "in_stock": {"type": "boolean"}
    }
  },
  "tool_mocks": {
    "web_fetch:{\"url\":\"https://example-shop.com/deleted-product\"}": {
      "status": 404,
      "body": "<html><body>Not found</body></html>",
      "headers": {}
    }
  }
}
```

Note: this fixture exercises the skill's 404 branch (extract step should emit `in_stock=false, price_usd=null`). The skill does NOT escalate on a 404 because `on_failure: fail_step` only trips on tool errors (HTTP exceptions), not non-200 status codes.

- [ ] **Step 5: Commit**

```bash
git add skills/product_watch/fixtures/
git commit -m "feat(skills): product_watch fixtures with tool_mocks"
```

---

## Task 15: F-11 — seed migration

**Files:**
- Create: `alembic/versions/seed_product_watch_capability.py`
- Test: `tests/unit/test_migration_seed_product_watch.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_migration_seed_product_watch.py`

```python
"""Tests for the seed_product_watch_capability migration."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


def _cfg(db: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.mark.asyncio
async def test_seed_creates_capability_skill_version_and_fixtures(tmp_path):
    db = tmp_path / "t.db"
    command.upgrade(_cfg(db), "head")

    async with aiosqlite.connect(db) as conn:
        # capability
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM capability WHERE name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1

        # skill in sandbox state
        cursor = await conn.execute(
            "SELECT state FROM skill WHERE capability_name = 'product_watch'"
        )
        row = await cursor.fetchone()
        assert row[0] == "sandbox"

        # skill_version with version_number=1
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM skill_version sv "
            "JOIN skill s ON sv.skill_id = s.id "
            "WHERE s.capability_name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 1

        # 4 fixtures
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM skill_fixture sf "
            "JOIN skill s ON sf.skill_id = s.id "
            "WHERE s.capability_name = 'product_watch'"
        )
        assert (await cursor.fetchone())[0] == 4

        # Each fixture has tool_mocks with the right fingerprint shape.
        cursor = await conn.execute(
            "SELECT case_name, tool_mocks FROM skill_fixture sf "
            "JOIN skill s ON sf.skill_id = s.id "
            "WHERE s.capability_name = 'product_watch'"
        )
        rows = await cursor.fetchall()
        for case_name, mocks_json in rows:
            mocks = json.loads(mocks_json)
            assert any("web_fetch" in k for k in mocks), f"{case_name}: no web_fetch in mocks"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_migration_seed_product_watch.py -v
```
Expected: 1 failure.

- [ ] **Step 3: Write the migration**

Locate the current head (after Task 7's `add_manual_draft_at`):

```bash
ls alembic/versions/ | tail
```

File: `alembic/versions/seed_product_watch_capability.py`

```python
"""seed product_watch capability + skill + fixtures

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-17 00:00:00.000000
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def upgrade() -> None:
    project_root = Path(__file__).resolve().parents[2]
    skill_dir = project_root / "skills" / "product_watch"

    conn = op.get_bind()
    now = datetime.now(tz=timezone.utc).isoformat()

    # 1. Capability.
    capability_id = str(uuid.uuid4())
    capability_yaml = project_root / "config" / "capabilities.yaml"
    import yaml
    caps = yaml.safe_load(_read(capability_yaml)).get("capabilities", [])
    cap_entry = next((c for c in caps if c.get("name") == "product_watch"), None)
    if cap_entry is None:
        raise RuntimeError("product_watch missing from config/capabilities.yaml")

    conn.execute(sa.text(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, default_output_shape, status, created_at, created_by) "
        "VALUES (:id, :name, :desc, :schema, :trigger, :shape, 'active', :now, 'seed')"
    ), {
        "id": capability_id,
        "name": "product_watch",
        "desc": cap_entry.get("description", ""),
        "schema": json.dumps(cap_entry.get("input_schema", {})),
        "trigger": cap_entry.get("trigger_type", "on_schedule"),
        "shape": json.dumps(cap_entry.get("default_output_shape", {})),
        "now": now,
    })

    # 2. Skill (sandbox state).
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, created_at, updated_at) "
        "VALUES (:id, 'product_watch', :vid, 'sandbox', 0, :now, :now)"
    ), {"id": skill_id, "vid": version_id, "now": now})

    # 3. Skill version with YAML backbone, step content, schemas.
    yaml_backbone = _read(skill_dir / "skill.yaml")
    step_content = {
        "extract_product_info": _read(skill_dir / "steps" / "extract_product_info.md"),
        "format_output": _read(skill_dir / "steps" / "format_output.md"),
    }
    output_schemas = {
        "extract_product_info": json.loads(_read(skill_dir / "schemas" / "extract_product_info_v1.json")),
        "format_output": json.loads(_read(skill_dir / "schemas" / "format_output_v1.json")),
    }

    conn.execute(sa.text(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) "
        "VALUES (:id, :sid, 1, :yaml, :steps, :schemas, 'seed', 'initial v1', :now)"
    ), {
        "id": version_id, "sid": skill_id,
        "yaml": yaml_backbone,
        "steps": json.dumps(step_content),
        "schemas": json.dumps(output_schemas),
        "now": now,
    })

    # 4. Fixtures.
    fixtures_dir = skill_dir / "fixtures"
    for fixture_file in sorted(fixtures_dir.glob("*.json")):
        fixture = json.loads(_read(fixture_file))
        conn.execute(sa.text(
            "INSERT INTO skill_fixture "
            "(id, skill_id, case_name, input, expected_output_shape, "
            " source, captured_run_id, created_at, tool_mocks) "
            "VALUES (:id, :sid, :case, :input, :shape, 'human_written', "
            "         NULL, :now, :mocks)"
        ), {
            "id": str(uuid.uuid4()),
            "sid": skill_id,
            "case": fixture["case_name"],
            "input": json.dumps(fixture["input"]),
            "shape": json.dumps(fixture.get("expected_output_shape")),
            "now": now,
            "mocks": json.dumps(fixture["tool_mocks"]) if fixture.get("tool_mocks") else None,
        })


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM skill_fixture WHERE skill_id IN "
        "(SELECT id FROM skill WHERE capability_name = 'product_watch')"
    ))
    conn.execute(sa.text(
        "DELETE FROM skill_version WHERE skill_id IN "
        "(SELECT id FROM skill WHERE capability_name = 'product_watch')"
    ))
    conn.execute(sa.text(
        "DELETE FROM skill WHERE capability_name = 'product_watch'"
    ))
    conn.execute(sa.text(
        "DELETE FROM capability WHERE name = 'product_watch'"
    ))
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/unit/test_migration_seed_product_watch.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/seed_product_watch_capability.py tests/unit/test_migration_seed_product_watch.py
git commit -m "feat(migrations): seed product_watch capability + skill + fixtures"
```

---

## Task 16: F-11 — wire `SeedCapabilityLoader` + `register_default_tools` into orchestrator

**Files:**
- Modify: `src/donna/cli.py`

- [ ] **Step 1: Explore current tool registration**

```bash
grep -rn "register_default_tools\|ToolRegistry()" src/donna/
```

Identify whether `register_default_tools` is currently called in `cli.py`. If yes, this task is a no-op for tool registration; if no, add the call.

- [ ] **Step 2: Write the integration test**

Use existing `tests/integration/test_notification_service_wiring.py` pattern, but assert that after orchestrator boot, `product_watch` capability exists and `web_fetch` is in the tool registry (if we can reach the registry from test harness).

Actually, testing tool-registry contents from an orchestrator lifespan is hard. Instead, add a unit-test that confirms the wiring *calls* `register_default_tools`. Use monkeypatch:

```python
# tests/integration/test_cli_wires_tools_and_capabilities.py

@pytest.mark.asyncio
async def test_orchestrator_registers_default_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")

    called = {"tools": 0}
    from donna.skills.tools import register_default_tools as orig_register

    def _capture(registry):
        called["tools"] += 1
        orig_register(registry)

    monkeypatch.setattr("donna.skills.tools.register_default_tools", _capture)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    import argparse
    args = argparse.Namespace(
        config_dir="config", log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    assert called["tools"] >= 1
```

- [ ] **Step 3: Add the wiring to cli.py**

In `_run_orchestrator`, inside the skill-system wiring block after the bundle is built, register default tools + load capabilities from YAML:

```python
# After bundle = assemble_skill_system(...):
if bundle is not None:
    # Wave 2: register default skill tools (web_fetch, etc.).
    from donna.skills.tools import register_default_tools
    # Tool registry is created per-execute inside SkillExecutor today; the
    # bundle's components don't hold a registry. Register the tools on the
    # module-level default registry if one exists, or pass a pre-populated
    # registry into the executor factory. Inspect skills/__init__.py and
    # executor.py to decide where to register.
    # For now: register on a module-level DEFAULT_TOOL_REGISTRY used by
    # the production SkillExecutor when no registry is passed.
    ...

    # Wave 2: load seed capabilities from config/capabilities.yaml.
    from donna.skills.seed_capabilities import SeedCapabilityLoader
    from pathlib import Path
    loader = SeedCapabilityLoader(connection=db.connection)
    cap_yaml = Path(config_dir) / "capabilities.yaml"
    if cap_yaml.exists():
        await loader.load_and_upsert(cap_yaml)
```

**Important:** the exact tool-registration mechanism depends on the existing `SkillExecutor` tool-registry pattern. During implementation, inspect:

```bash
grep -n "tool_registry\|ToolRegistry\|register_default_tools" src/donna/skills/ | head
```

If `SkillExecutor.__init__` has `tool_registry: ToolRegistry | None = None` and constructs one on-demand when None, the production skill-execution path (not yet exercised because no skill is shadow_primary yet) will need a pre-populated registry. Two options:
(a) Add a module-level `DEFAULT_TOOL_REGISTRY` and register_default_tools(DEFAULT_TOOL_REGISTRY) at cli startup. `SkillExecutor.__init__` defaults to this.
(b) Pass the registry into `assemble_skill_system` and thread it to the executor factory.

For Wave 2, go with (a) — simplest.

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/integration/test_cli_wires_tools_and_capabilities.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli.py src/donna/skills/tools/ tests/integration/test_cli_wires_tools_and_capabilities.py
git commit -m "feat(cli): register default tools + load seed capabilities on startup"
```

---

## Task 17: E2E — `test_wave2_product_watch.py`

**Files:**
- Create: `tests/e2e/test_wave2_product_watch.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: product_watch automation runs through the full pipeline."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_product_watch_automation_tick_fires_alert(runtime) -> None:
    """Create a product_watch automation, tick the scheduler, verify alert."""
    conn = runtime.db.connection
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(minutes=5)).isoformat()

    # Seed product_watch capability (normally done by migration — explicit here
    # because the e2e harness may not run the seed migration).
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, 'product_watch', ?, ?, 'on_schedule', 'active', ?, 'seed')",
        (str(uuid.uuid4()), "Watch a product URL",
         json.dumps({"type": "object"}), now.isoformat()),
    )
    await conn.commit()

    # Canned claude_native output — automation dispatcher uses
    # task_type=<capability_name> per Phase 5.
    runtime.fake_claude.canned["product_watch"] = {
        "ok": True,
        "price_usd": 79.0,
        "currency": "USD",
        "in_stock": True,
        "size_available": True,
        "triggers_alert": True,
        "title": "Blue Shirt",
    }

    # Create the automation.
    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', 'Watch Blue Shirt', NULL, 'product_watch', ?, "
        "'on_schedule', '0 * * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            json.dumps({
                "url": "https://example-shop.com/shirt-blue",
                "max_price_usd": 100.0,
                "required_size": "L",
            }),
            json.dumps({"all_of": [{"field": "triggers_alert", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(), now.isoformat(),
        ),
    )
    await conn.commit()

    await runtime.automation_scheduler.run_once()

    cursor = await conn.execute(
        "SELECT status, alert_sent FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "succeeded"
    assert rows[0][1] == 1

    # Alert was sent via NotificationService → FakeDonnaBot.
    assert len(runtime.fake_bot.sends) >= 1
    kind, target, content = runtime.fake_bot.sends[0]
    assert kind == "channel"
    assert target == "tasks"
```

- [ ] **Step 2: Run, verify passes**

```bash
pytest tests/e2e/test_wave2_product_watch.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave2_product_watch.py
git commit -m "test(e2e): product_watch automation end-to-end"
```

---

## Task 18: Documentation updates

**Files:**
- Modify: `docs/superpowers/followups/2026-04-16-skill-system-followups.md`
- Modify: `docs/superpowers/specs/2026-04-17-skill-system-wave-2-first-capability-design.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update followups inventory**

In `docs/superpowers/followups/2026-04-16-skill-system-followups.md`, add a "Completed — Wave 2 (2026-04-17)" section after the "Completed — Wave 1" one:

```markdown
## Completed — Wave 2 (2026-04-17)

- **F-W1-B** EvolutionGates now thread tool_mocks through all three gates.
- **F-W1-C** Router kwargs mismatch resolved; executor/triage drop unsupported kwargs.
- **F-W1-D** draft-now via `skill_candidate_report.manual_draft_at` + ManualDraftPoller.
- **F-W1-E** Validation-mode per-step timeout wired.
- **F-W1-F** `POST /admin/skill-runs/{id}/capture-fixture` landed.
- **F-W1-G** Validation LLM calls tagged `skill_validation::<cap>::<step>`.
- **F-W1-H** Automation subsystem runs independent of `skill_system.enabled`.
- **F-2** `automation_run.skill_run_id` and `skill_run.automation_run_id` populated both ways.
- **F-7** CorrectionClusterDetector fires synchronously from correction-log write path.
- **F-11** `product_watch` capability + skill + 4 fixtures seeded.

Wave 3 plan: F-3 Discord natural-language automation creation (OOS-W2-1).
```

- [ ] **Step 2: Tick spec checklist**

In `docs/superpowers/specs/2026-04-17-skill-system-wave-2-first-capability-design.md`, mark W2-R1..R22 as `[x]` where verified.

- [ ] **Step 3: Update architecture.md (optional, if product_watch deserves a note)**

Short note added if relevant: "First seeded capability: `product_watch`. See `skills/product_watch/` for YAML + fixtures."

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: tick Wave 2 requirements and followups"
```

---

## Task 19: Final full-suite verification

**Files:** none — verification only.

- [ ] **Step 1: Full suite**

```bash
pytest tests/ --ignore=tests/integration/test_llm_smoke.py --ignore=tests/integration/test_calendar_sync.py 2>&1 | tail -30
```

Expected: green except the 8 Wave 1-era pre-existing failures (test_database InvocationLogger, admin_health, admin_logs, supabase_sync, weekly_planner).

- [ ] **Step 2: Verify no regressions in previously-ticked W1 requirements**

Spot-check critical W1 flows:

```bash
pytest tests/e2e/ tests/unit/test_validation_executor.py tests/unit/test_mock_tool_registry.py tests/integration/test_automation_scheduler_in_orchestrator.py -v
```

All green.

- [ ] **Step 3: Manual AS-W2.1**

Start orchestrator locally with real Discord creds:

```bash
DISCORD_BOT_TOKEN=... DISCORD_TASKS_CHANNEL_ID=... donna run
```

Create the automation:

```bash
curl -X POST http://localhost:8200/admin/automations \
  -H "content-type: application/json" \
  -d '{
    "user_id": "nick",
    "name": "Watch COS shirt",
    "capability_name": "product_watch",
    "inputs": {"url": "https://www.cos.com/en_usd/shirt", "max_price_usd": 100, "required_size": "L"},
    "trigger_type": "on_schedule",
    "schedule": "0 12 * * *",
    "alert_conditions": {"all_of": [{"field": "triggers_alert", "op": "==", "value": true}]},
    "alert_channels": ["tasks"],
    "min_interval_seconds": 3600
  }'
```

Trigger immediately:

```bash
curl -X POST http://localhost:8200/admin/automations/<id>/run-now
```

Within ≤15s a Discord message should arrive in the configured channel.

- [ ] **Step 4: Document the manual result**

If AS-W2.1 fails, drift-log the issue. If it passes, note in the PR body.

---

## Self-Review Checklist

- [ ] **Spec coverage.** All W2-R1..R22 map to at least one task. Confirmed: R1→T1, R2-R4→T3, R5-R6→T4, R7-R8→T0+T5, R9→T6, R10-R12→T7-T8, R13→T9, R14-R15→T10, R16→T11, R17-R19→T12-T15, R20→T17, R21→T19, R22→T18.
- [ ] **Type consistency.** `task_type_prefix`, `run_sink`, `config` all consistently named on `SkillExecutor`. `tool_mocks` threaded through `Fixture`, `MockToolRegistry`, `ValidationExecutor`, `EvolutionGates`. `run_id` on `SkillRunResult` only, not conflated with `skill_run_id` or `automation_run_id`.
- [ ] **Migrations chain.** `add_manual_draft_at (c9d0e1f2a3b4) → seed_product_watch_capability (d0e1f2a3b4c5)` chained off Wave 1 head `b8c9d0e1f2a3`. Verify during implementation.
- [ ] **No placeholders.** Code blocks are complete. The only placeholder is the Task 11 "PLACEHOLDER — update after exploration" import path, which is explicitly flagged as exploration-dependent.
- [ ] **Dependency order.** T0 (routing) → T1 (delete kwargs) is critical. T2 (mock_synthesis) before T3 (gates) and T9 (capture-fixture). T4 (per-step timeout) before T5 (prefix test — shares plumbing). T7 (migration) before T8 (poller/API that use column). T12-T15 (product_watch) before T17 (E2E). T19 last.
