# Task Evaluation: Domain & Duration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Donna's task parsing produce accurate work/personal domains and realistic durations, running local-first (qwen2.5:32b) with a confidence-gated Claude fallback, and revive the dormant correction-learning loop so corrections teach future parses.

**Architecture:** Five changes to the parse pipeline: (1) a calibrated prompt with duration anchors and a personal-context slot; (2) a context provider that pulls active preference rules + vault notes; (3) confidence-gated escalation to Claude in `InputParser`; (4) a routing flip to local-first; (5) an API/UI edit pathway for `domain`/`estimated_duration` that fires the existing `correction_subscriber` chain. The `PreferenceApplier` and `MemoryStore` (already built elsewhere) get wired into the parser.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite (aiosqlite), Ollama (qwen2.5:32b) + Anthropic via `ModelRouter`, pytest/pytest-asyncio; React + TypeScript + TanStack Table for the UI.

**Spec:** `docs/superpowers/specs/2026-06-10-task-evaluation-domain-duration-design.md`

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `prompts/parse_task.md` | Modify | Duration anchors, sharper domain rubric, `{{ personal_context }}` slot |
| `src/donna/orchestrator/input_parser.py` | Modify | Render context, confidence-gated escalation, accept `memory_store` |
| `src/donna/orchestrator/task_context.py` | Create | Build the personal-context block (prefs + vault) |
| `src/donna/models/router.py` | Modify | `confidence_threshold_for(task_type)` accessor |
| `config/donna_models.yaml` | Modify | Flip `parse_task` to local-first; add `parse_task_cloud` |
| `src/donna/cli_wiring.py` | Modify | Instantiate `PreferenceApplier`, pass to `InputParser` |
| `src/donna/cli.py` | Modify | Late-bind `memory_store` into the parser after it's built |
| `src/donna/api/routes/tasks.py` | Modify | Allow editing `domain`/`estimated_duration` |
| `src/donna/api/routes/admin_tasks.py` | Modify | `PATCH /admin/tasks/{id}` edit endpoint (UI path) |
| `donna-ui/src/api/tasks.ts` | Modify | `updateTask()` client fn |
| `donna-ui/src/pages/Tasks/TaskDetailExpander.tsx` | Modify | Inline edit control for domain + duration |
| `tests/unit/test_input_parser.py` | Modify | Render, context, escalation tests |
| `tests/unit/test_task_context.py` | Create | Context-provider tests |
| `tests/unit/test_router_confidence.py` | Create | Threshold accessor test |
| `tests/api/test_tasks_update.py` | Create | Edit-pathway route tests |

---

## Task 1: Personal-context render plumbing

Add a `{{ personal_context }}` slot to the prompt and teach `_render_template` to fill it (graceful default when empty). No behaviour change to durations yet — this is the plumbing the context provider plugs into.

**Files:**
- Modify: `src/donna/orchestrator/input_parser.py` (`_render_template`, lines ~52-62)
- Modify: `prompts/parse_task.md`
- Test: `tests/unit/test_input_parser.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_input_parser.py` inside `class TestRenderTemplate`:

```python
    def test_fills_personal_context(self) -> None:
        template = "Context:\n{{ personal_context }}\nEnd"
        result = _render_template(template, "test", personal_context="Knows: Alice (coworker)")
        assert "Alice (coworker)" in result
        assert "{{ personal_context }}" not in result

    def test_personal_context_defaults_to_none_marker(self) -> None:
        template = "Context: {{ personal_context }}"
        result = _render_template(template, "test")
        assert "{{ personal_context }}" not in result
        assert "(none)" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_input_parser.py::TestRenderTemplate::test_fills_personal_context -v`
Expected: FAIL — `_render_template() got an unexpected keyword argument 'personal_context'`

- [ ] **Step 3: Update `_render_template`**

Replace the function in `src/donna/orchestrator/input_parser.py`:

```python
def _render_template(
    template: str,
    user_input: str,
    tz: zoneinfo.ZoneInfo | None = None,
    personal_context: str = "",
) -> str:
    """Fill template variables with current context."""
    now = datetime.now(UTC).astimezone(tz or _DEFAULT_TZ)
    return (
        template
        .replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
        .replace("{{ current_time }}", now.strftime("%I:%M %p %Z"))
        .replace("{{ personal_context }}", personal_context.strip() or "(none)")
        .replace("{{ user_input }}", user_input)
    )
```

- [ ] **Step 4: Add the slot to `prompts/parse_task.md`**

Insert this section immediately **before** the `## User Input` section:

```markdown
## Personal Context

The following are known people, projects, and learned preferences for this
user. Use them to disambiguate domain (work vs personal vs family) and to
calibrate effort. If this says "(none)", rely on the rubric alone.

{{ personal_context }}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_input_parser.py::TestRenderTemplate -v`
Expected: PASS (all render tests)

- [ ] **Step 6: Commit**

```bash
git add src/donna/orchestrator/input_parser.py prompts/parse_task.md tests/unit/test_input_parser.py
git commit -m "feat: add personal-context slot to task parse prompt"
```

---

## Task 2: Calibrate duration anchors and domain rubric

Rewrite the duration and domain guidance in the prompt so the model anchors low and only inflates with justification. This is the change that directly fixes "everything is an hour."

**Files:**
- Modify: `prompts/parse_task.md`
- Test: `tests/unit/test_input_parser.py`

- [ ] **Step 1: Write the failing guard test**

Add a new class to `tests/unit/test_input_parser.py`:

```python
class TestParsePromptCalibration:
    def test_prompt_contains_duration_anchors(self) -> None:
        text = (PROJECT_ROOT / "prompts" / "parse_task.md").read_text()
        # Quick-comms anchor and the "default low" instruction must be present.
        assert "15" in text
        assert "30" in text
        assert "60" in text
        assert "lower anchor" in text.lower()

    def test_prompt_lists_quick_comm_examples(self) -> None:
        text = (PROJECT_ROOT / "prompts" / "parse_task.md").read_text().lower()
        for example in ("email", "schedule", "touch base"):
            assert example in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_input_parser.py::TestParsePromptCalibration -v`
Expected: FAIL — "lower anchor" / "touch base" not yet in the prompt

- [ ] **Step 3: Replace the duration line and add a rubric**

In `prompts/parse_task.md`, in the Output Schema JSON, change the `estimated_duration` line to:

```json
  "estimated_duration": "minutes as integer — see Duration Guidelines (default: 15)",
```

Then add this section immediately **after** the `## Domain Inference` section:

```markdown
## Duration Guidelines

Estimate the *focused working time*, not elapsed calendar time. Default to the
lower anchor; only inflate when the task text explicitly justifies more effort
(e.g. "write the full Q3 report", "deep clean the garage").

- **15 min** — quick communications and micro-tasks: send an email or text,
  a phone call or message to schedule an appointment, touch base with someone,
  RSVP, confirm a time, pay a single bill, a quick lookup.
- **30 min** — short admin and errands: fill out a form, a focused errand,
  review a short document, a brief 1:1.
- **60 min** — sustained work or meetings: writing, coding, a standard meeting,
  anything requiring uninterrupted focus.
- **>60 min** — only when the text names a clearly large effort. State why in
  the description.

When unsure between two anchors, pick the lower one and lower your confidence.
```

Also sharpen the domain section — replace the `## Domain Inference` body with:

```markdown
- **personal**: Health, car, home maintenance, hobbies, personal finance,
  shopping, personal appointments, friends.
- **work**: Professional projects, work meetings, code, professional
  development, communication with colleagues or clients.
- **family**: Child care, family events, family obligations, shared household
  tasks.

Many tasks are ambiguous from the text alone (an email, "touch base with
someone", "call about the appointment"). Use the Personal Context section to
resolve them — if a named person or project there is work-related, lean work;
if personal, lean personal. When the context does not resolve it, pick the most
likely domain and **lower your confidence below 0.7** so the system can
escalate.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_input_parser.py::TestParsePromptCalibration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add prompts/parse_task.md tests/unit/test_input_parser.py
git commit -m "feat: calibrate duration anchors and domain rubric in parse prompt"
```

---

## Task 3: Personal-context provider

A standalone module that turns a user's active preference rules + top vault hits into a compact text block. Pure and independently testable; degrades to `""` when nothing is available.

**Files:**
- Create: `src/donna/orchestrator/task_context.py`
- Test: `tests/unit/test_task_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_task_context.py`:

```python
"""Tests for the personal-context provider."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from donna.orchestrator.task_context import build_personal_context


@dataclass
class FakeChunk:
    title: str | None
    content: str


class FakeMemoryStore:
    def __init__(self, hits: list[FakeChunk]) -> None:
        self._hits = hits
        self.search = AsyncMock(return_value=hits)


class FakeApplier:
    def __init__(self, rules: list[dict]) -> None:
        self._rules = rules
        self.load_rules = AsyncMock(return_value=rules)


async def test_empty_when_no_sources() -> None:
    result = await build_personal_context(
        "send an email", "nick", preference_applier=None, memory_store=None,
    )
    assert result == ""


async def test_includes_vault_titles_and_pref_hints() -> None:
    store = FakeMemoryStore([FakeChunk(title="Alice Smith", content="Coworker on Project X.")])
    applier = FakeApplier([
        {"condition": {"keywords": ["dentist"]}, "action": {"field": "domain", "value": "personal"}},
    ])
    result = await build_personal_context(
        "email Alice about Project X", "nick",
        preference_applier=applier, memory_store=store,
    )
    assert "Alice Smith" in result
    assert "Coworker on Project X" in result
    assert "dentist" in result
    assert "personal" in result
    store.search.assert_awaited_once()


async def test_survives_memory_store_error() -> None:
    store = FakeMemoryStore([])
    store.search = AsyncMock(side_effect=RuntimeError("vec0 down"))
    result = await build_personal_context(
        "email Alice", "nick", preference_applier=None, memory_store=store,
    )
    assert result == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_task_context.py -v`
Expected: FAIL — `ModuleNotFoundError: donna.orchestrator.task_context`

- [ ] **Step 3: Implement the provider**

Create `src/donna/orchestrator/task_context.py`:

```python
"""Personal-context provider for task parsing.

Assembles a compact text block from the user's active learned-preference
rules and top-k vault notes, injected into the parse prompt so the model can
disambiguate domain and calibrate effort. Degrades to an empty string when no
sources are available or any source errors. See docs/task-system.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from donna.memory.store import MemoryStore
    from donna.preferences.rule_applier import PreferenceApplier

logger = structlog.get_logger()

_MAX_NOTES = 3
_MAX_RULES = 5


async def build_personal_context(
    raw_text: str,
    user_id: str,
    *,
    preference_applier: PreferenceApplier | None,
    memory_store: MemoryStore | None,
) -> str:
    """Return a compact context block, or "" when nothing is available.

    Never raises — a failure in any source degrades to less context, not an
    error, because parsing must not be blocked by retrieval problems.
    """
    sections: list[str] = []

    notes = await _vault_notes(raw_text, user_id, memory_store)
    if notes:
        sections.append("Known people & projects:\n" + notes)

    rules = await _preference_hints(user_id, preference_applier)
    if rules:
        sections.append("Learned preferences:\n" + rules)

    return "\n\n".join(sections)


async def _vault_notes(
    raw_text: str, user_id: str, memory_store: MemoryStore | None
) -> str:
    if memory_store is None:
        return ""
    try:
        hits = await memory_store.search(
            query=raw_text, user_id=user_id, k=_MAX_NOTES, sources=["vault"],
        )
    except Exception as exc:  # noqa: BLE001 — retrieval must never block parsing
        logger.warning("task_context_vault_failed", reason=str(exc), user_id=user_id)
        return ""

    lines: list[str] = []
    for hit in hits[:_MAX_NOTES]:
        title = getattr(hit, "title", None) or "(untitled)"
        snippet = " ".join(getattr(hit, "content", "").split())[:160]
        lines.append(f"- {title}: {snippet}")
    return "\n".join(lines)


async def _preference_hints(
    user_id: str, preference_applier: PreferenceApplier | None
) -> str:
    if preference_applier is None:
        return ""
    try:
        rules: list[dict[str, Any]] = await preference_applier.load_rules(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_context_prefs_failed", reason=str(exc), user_id=user_id)
        return ""

    lines: list[str] = []
    for rule in rules[:_MAX_RULES]:
        condition = rule.get("condition", {})
        action = rule.get("action", {})
        keywords = ", ".join(condition.get("keywords", [])) or condition.get("domain", "any")
        field = action.get("field")
        value = action.get("value")
        if field and value is not None:
            lines.append(f"- when [{keywords}] → {field} = {value}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_task_context.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/donna/orchestrator/task_context.py tests/unit/test_task_context.py
git commit -m "feat: add personal-context provider for task parsing"
```

---

## Task 4: Inject context into InputParser + memory_store late-binding

Wire the provider into `InputParser.parse`, and add a `set_memory_store()` late-binder (the store is built after the parser at boot, mirroring the router's `set_fallback_alert_fn` pattern).

**Files:**
- Modify: `src/donna/orchestrator/input_parser.py`
- Test: `tests/unit/test_input_parser.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_input_parser.py` inside `class TestInputParser`:

```python
    async def test_personal_context_injected_into_prompt(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        from unittest.mock import AsyncMock as _AM

        router.complete = _AM(return_value=(_buy_milk_response(), _make_metadata()))
        parser = InputParser(router, mock_logger, PROJECT_ROOT)

        class _Store:
            search = _AM(return_value=[type("H", (), {"title": "Alice", "content": "Coworker"})()])

        parser.set_memory_store(_Store())
        await parser.parse("email Alice", user_id="nick")

        called_prompt = router.complete.call_args[0][0]
        assert "Alice" in called_prompt
        assert "(none)" not in called_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_input_parser.py::TestInputParser::test_personal_context_injected_into_prompt -v`
Expected: FAIL — `InputParser` has no attribute `set_memory_store`

- [ ] **Step 3: Implement the wiring**

In `src/donna/orchestrator/input_parser.py`:

Add the import near the top (after the existing `from donna.tasks.dedup import ...`):

```python
from donna.orchestrator.task_context import build_personal_context
```

Add `memory_store` to `__init__` (new param at the end) and store it:

```python
        tz: zoneinfo.ZoneInfo | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self._router = router
        self._invocation_logger = invocation_logger
        self._project_root = project_root
        self._deduplicator = deduplicator
        self._preference_applier = preference_applier
        self._tz = tz
        self._memory_store = memory_store
```

Add a late-binder method to the class:

```python
    def set_memory_store(self, memory_store: Any) -> None:
        """Late-bind the memory store (built after the parser at boot)."""
        self._memory_store = memory_store
```

In `parse()`, replace the `# 1. Render prompt template` block with:

```python
        # 1. Build personal context, then render the prompt template
        personal_context = await build_personal_context(
            raw_text,
            user_id,
            preference_applier=self._preference_applier,
            memory_store=self._memory_store,
        )
        template = self._router.get_prompt_template(TASK_TYPE)
        prompt = _render_template(
            template, raw_text, tz=self._tz, personal_context=personal_context,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_input_parser.py -v`
Expected: PASS (all existing + new test)

- [ ] **Step 5: Commit**

```bash
git add src/donna/orchestrator/input_parser.py tests/unit/test_input_parser.py
git commit -m "feat: inject personal context into task parsing"
```

---

## Task 5: Confidence-gated escalation to Claude

Add a router accessor for the configured threshold, then escalate the parse to the cloud model when the local model returns low confidence.

**Files:**
- Modify: `src/donna/models/router.py`
- Modify: `src/donna/orchestrator/input_parser.py`
- Test: `tests/unit/test_router_confidence.py` (create), `tests/unit/test_input_parser.py`

- [ ] **Step 1: Write the failing router test**

Create `tests/unit/test_router_confidence.py`:

```python
"""Tests for the router confidence-threshold accessor."""

from __future__ import annotations

from pathlib import Path

from donna.config import (
    ModelConfig,
    ModelsConfig,
    RoutingEntry,
    TaskTypesConfig,
)
from donna.models.router import ModelRouter

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _router() -> ModelRouter:
    cfg = ModelsConfig(
        models={"parser": ModelConfig(provider="anthropic", model="x")},
        routing={
            "parse_task": RoutingEntry(model="parser", confidence_threshold=0.7),
            "no_threshold": RoutingEntry(model="parser"),
        },
    )
    return ModelRouter(cfg, TaskTypesConfig(task_types={}), PROJECT_ROOT)


def test_returns_configured_threshold() -> None:
    assert _router().confidence_threshold_for("parse_task") == 0.7


def test_returns_none_when_unset() -> None:
    assert _router().confidence_threshold_for("no_threshold") is None


def test_returns_none_for_unknown_task() -> None:
    assert _router().confidence_threshold_for("bogus") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_router_confidence.py -v`
Expected: FAIL — `ModelRouter` has no attribute `confidence_threshold_for`

- [ ] **Step 3: Add the accessor**

In `src/donna/models/router.py`, add this method to `ModelRouter` (near `_lookup_routing_entry`):

```python
    def confidence_threshold_for(self, task_type: str) -> float | None:
        """Return the configured confidence threshold for ``task_type``, if any."""
        entry = self._lookup_routing_entry(task_type)
        return getattr(entry, "confidence_threshold", None) if entry else None
```

- [ ] **Step 4: Run the router test to verify it passes**

Run: `uv run pytest tests/unit/test_router_confidence.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing escalation test**

Add to `tests/unit/test_input_parser.py` inside `class TestInputParser`. Note the `models_config` fixture already sets `confidence_threshold=0.7`:

```python
    async def test_low_confidence_escalates_to_cloud(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        from unittest.mock import AsyncMock as _AM

        low = _buy_milk_response() | {"confidence": 0.4, "domain": "personal"}
        high = _buy_milk_response() | {"confidence": 0.95, "domain": "work"}
        router.complete = _AM(side_effect=[(low, _make_metadata()), (high, _make_metadata())])

        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        result = await parser.parse("email the client", user_id="nick")

        assert router.complete.await_count == 2
        assert router.complete.await_args_list[1].kwargs["task_type"] == "parse_task_cloud"
        assert result.domain == "work"  # cloud result wins
        assert result.confidence == 0.95

    async def test_high_confidence_does_not_escalate(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        from unittest.mock import AsyncMock as _AM

        router.complete = _AM(return_value=(_buy_milk_response(), _make_metadata()))
        parser = InputParser(router, mock_logger, PROJECT_ROOT)
        await parser.parse("Buy milk", user_id="nick")
        assert router.complete.await_count == 1
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_input_parser.py::TestInputParser::test_low_confidence_escalates_to_cloud -v`
Expected: FAIL — only one `complete` call (no escalation logic yet)

- [ ] **Step 7: Implement escalation in `parse()`**

In `src/donna/orchestrator/input_parser.py`, add a module constant near `TASK_TYPE`:

```python
TASK_TYPE = "parse_task"
CLOUD_TASK_TYPE = "parse_task_cloud"
```

Replace the validate block (steps "# 2. Call the model" and "# 3. Validate") with:

```python
        # 2. Call the model (invocation logged automatically by ModelRouter)
        response, _metadata = await self._router.complete(
            prompt, task_type=TASK_TYPE, user_id=user_id,
        )

        # 3. Validate against schema
        schema = self._router.get_output_schema(TASK_TYPE)
        validated = validate_output(response, schema)

        # 3b. Confidence-gated escalation: re-parse on the cloud model when the
        # local model is unsure. The cloud route reuses this prompt + schema.
        threshold = self._router.confidence_threshold_for(TASK_TYPE)
        if threshold is not None and validated["confidence"] < threshold:
            logger.info(
                "parse_confidence_escalation",
                local_confidence=validated["confidence"],
                threshold=threshold,
                user_id=user_id,
            )
            cloud_response, _cloud_meta = await self._router.complete(
                prompt, task_type=CLOUD_TASK_TYPE, user_id=user_id,
            )
            validated = validate_output(cloud_response, schema)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_input_parser.py -v`
Expected: PASS (all, including both escalation tests)

- [ ] **Step 9: Commit**

```bash
git add src/donna/models/router.py src/donna/orchestrator/input_parser.py tests/unit/test_router_confidence.py tests/unit/test_input_parser.py
git commit -m "feat: confidence-gated cloud escalation for task parsing"
```

---

## Task 6: Flip routing to local-first

Make the local model primary for parsing and register the cloud escalation route. The `parse_task_cloud` key needs only a routing entry — `complete()` resolves the model from it; the prompt and schema come from the `parse_task` task type the caller already passes to `get_output_schema`.

**Files:**
- Modify: `config/donna_models.yaml`
- Test: `tests/unit/test_router_confidence.py`

- [ ] **Step 1: Write the failing config test**

Add to `tests/unit/test_router_confidence.py`:

```python
def test_real_config_routes_parse_task_local_first() -> None:
    import yaml

    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "donna_models.yaml").read_text())
    routing = cfg["routing"]
    assert routing["parse_task"]["model"] == "local_parser"
    assert routing["parse_task"]["fallback"] == "reasoner"
    assert routing["parse_task"]["confidence_threshold"] == 0.7
    assert routing["parse_task_cloud"]["model"] == "reasoner"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_router_confidence.py::test_real_config_routes_parse_task_local_first -v`
Expected: FAIL — `parse_task.model` is currently `parser`, and `parse_task_cloud` is absent

- [ ] **Step 3: Edit the routing config**

In `config/donna_models.yaml`, change the `parse_task` routing entry to:

```yaml
  parse_task:
    model: local_parser
    fallback: reasoner
    confidence_threshold: 0.7
```

And add, directly below it:

```yaml
  parse_task_cloud:
    model: reasoner
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_router_confidence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/donna_models.yaml tests/unit/test_router_confidence.py
git commit -m "feat: route task parsing local-first with cloud escalation"
```

---

## Task 7: Wire PreferenceApplier and memory_store into the live parser

`PreferenceApplier` is never instantiated in production wiring, and `memory_store` is built after the parser. Pass the applier at construction and late-bind the store. This is what actually turns the learning loop on end-to-end.

**Files:**
- Modify: `src/donna/cli_wiring.py` (around line 1252)
- Modify: `src/donna/cli.py` (after `memory_store` is built, ~line 286)
- Test: `tests/unit/test_input_parser.py` (applier integration at the parser seam)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_input_parser.py` inside `class TestInputParser`:

```python
    async def test_preference_applier_overrides_result(
        self, router: ModelRouter, mock_logger: AsyncMock
    ) -> None:
        from unittest.mock import AsyncMock as _AM

        router.complete = _AM(return_value=(_buy_milk_response(), _make_metadata()))

        class _Applier:
            async def apply_for_user(self, result, user_id):
                import dataclasses
                return dataclasses.replace(result, domain="work")

        parser = InputParser(
            router, mock_logger, PROJECT_ROOT, preference_applier=_Applier(),
        )
        result = await parser.parse("Buy milk", user_id="nick")
        assert result.domain == "work"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/unit/test_input_parser.py::TestInputParser::test_preference_applier_overrides_result -v`
Expected: PASS — `parse()` already calls `apply_for_user` when an applier is set. (This test pins the seam the wiring depends on. If it fails, fix `parse()` before changing wiring.)

- [ ] **Step 3: Instantiate PreferenceApplier in wiring**

In `src/donna/cli_wiring.py`, change the parser construction (line ~1252) from:

```python
    input_parser = InputParser(router, invocation_logger, project_root, tz=user_tz)
```

to:

```python
    from donna.preferences.rule_applier import PreferenceApplier

    preference_applier = PreferenceApplier(db)
    input_parser = InputParser(
        router, invocation_logger, project_root,
        tz=user_tz, preference_applier=preference_applier,
    )
```

- [ ] **Step 4: Late-bind memory_store in cli.py**

In `src/donna/cli.py`, immediately after the `memory_store, memory_handles = await _try_build_memory_store(...)` call (~line 286-292), add:

```python
    if memory_store is not None and getattr(ctx, "input_parser", None) is not None:
        ctx.input_parser.set_memory_store(memory_store)
```

(If the second `_try_build_memory_store` call site at ~line 799 builds a parser-bound store too, apply the same two lines there.)

- [ ] **Step 5: Run the parser tests + a boot smoke test**

Run: `uv run pytest tests/unit/test_input_parser.py -v`
Expected: PASS

Run: `uv run pytest tests/integration/test_task_source_hook.py -v`
Expected: PASS (confirms parser/db wiring still boots)

- [ ] **Step 6: Commit**

```bash
git add src/donna/cli_wiring.py src/donna/cli.py tests/unit/test_input_parser.py
git commit -m "feat: wire preference applier and memory store into live parser"
```

---

## Task 8: API edit pathway for domain and duration

Add the two fields to `UpdateTaskRequest`. The route already calls `db.update_task(..., source="api")`, and `domain`/`estimated_duration` are already in `_UPDATABLE_COLUMNS`, so this single model change revives `correction_subscriber` for these fields.

**Files:**
- Modify: `src/donna/api/routes/tasks.py` (`UpdateTaskRequest`, lines ~78-82)
- Test: `tests/api/test_tasks_update.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_tasks_update.py`:

```python
"""Tests for the task edit pathway (domain + duration)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.api.routes.tasks import UpdateTaskRequest, update_task


@dataclass
class FakeRow:
    id: str = "t1"
    user_id: str = "nick"
    title: str = "Email the client"
    description: str | None = None
    domain: str = "personal"
    priority: int = 2
    status: str = "pending"
    estimated_duration: int | None = 60
    deadline: str | None = None
    deadline_type: str = "none"
    scheduled_start: str | None = None
    tags: list | None = None
    created_at: str = "2026-06-10T00:00:00"
    created_via: str | None = "app"


def _request_with_db(db) -> MagicMock:
    req = MagicMock()
    req.app.state.db = db
    return req


def test_update_request_accepts_domain_and_duration() -> None:
    body = UpdateTaskRequest(domain="work", estimated_duration=15)
    dumped = body.model_dump(exclude_none=True)
    assert dumped == {"domain": "work", "estimated_duration": 15}


def test_update_request_rejects_invalid_domain() -> None:
    with pytest.raises(ValueError):
        UpdateTaskRequest(domain="banana")


async def test_update_task_persists_with_api_source() -> None:
    row = FakeRow()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=row)
    db.update_task = AsyncMock(return_value=FakeRow(domain="work", estimated_duration=15))

    body = UpdateTaskRequest(domain="work", estimated_duration=15)
    result = await update_task(_request_with_db(db), "t1", body, user_id="nick")

    db.update_task.assert_awaited_once()
    assert db.update_task.await_args.kwargs["source"] == "api"
    assert db.update_task.await_args.kwargs["domain"] == "work"
    assert db.update_task.await_args.kwargs["estimated_duration"] == 15
    assert result.domain == "work"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_tasks_update.py -v`
Expected: FAIL — `UpdateTaskRequest` rejects `domain`/`estimated_duration` (unexpected kwargs)

- [ ] **Step 3: Extend UpdateTaskRequest**

In `src/donna/api/routes/tasks.py`, replace the `UpdateTaskRequest` class with:

```python
class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    status: str | None = None
    domain: str | None = None
    estimated_duration: int | None = None

    @field_validator("domain")
    @classmethod
    def _valid_domain(cls, v: str | None) -> str | None:
        if v is not None and v not in {"personal", "work", "family"}:
            raise ValueError("domain must be personal, work, or family")
        return v
```

Add `field_validator` to the existing pydantic import at the top of the file:

```python
from pydantic import BaseModel, field_validator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_tasks_update.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/tasks.py tests/api/test_tasks_update.py
git commit -m "feat: allow editing task domain and duration via API"
```

---

## Task 9: Admin PATCH endpoint + UI edit control

The dashboard reads tasks through `admin_tasks.py` (GET only) and `TaskDetailExpander.tsx`. Add a `PATCH /admin/tasks/{id}` endpoint that updates domain/duration with `source="dashboard"`, an `updateTask()` API client fn, and an inline editor in the detail panel. The `dashboard` source still fires `correction_subscriber`.

**Files:**
- Modify: `src/donna/api/routes/admin_tasks.py`
- Modify: `donna-ui/src/api/tasks.ts`
- Modify: `donna-ui/src/pages/Tasks/TaskDetailExpander.tsx`
- Test: `tests/api/test_tasks_update.py`

- [ ] **Step 1: Write the failing endpoint test**

Add to `tests/api/test_tasks_update.py`:

```python
async def test_admin_update_uses_dashboard_source() -> None:
    from donna.api.routes.admin_tasks import AdminTaskUpdate, update_task_admin

    row = FakeRow()
    db = MagicMock()
    db.get_task = AsyncMock(return_value=row)
    db.update_task = AsyncMock(return_value=FakeRow(estimated_duration=15))

    body = AdminTaskUpdate(estimated_duration=15)
    result = await update_task_admin(_request_with_db(db), "t1", body)

    assert db.update_task.await_args.kwargs["source"] == "dashboard"
    assert db.update_task.await_args.kwargs["estimated_duration"] == 15
    assert result["estimated_duration"] == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_tasks_update.py::test_admin_update_uses_dashboard_source -v`
Expected: FAIL — `AdminTaskUpdate`/`update_task_admin` do not exist

- [ ] **Step 3: Add the admin PATCH endpoint**

In `src/donna/api/routes/admin_tasks.py`, add the import (top of file, with the other imports):

```python
from pydantic import BaseModel, field_validator
```

Add the model and handler (place after the existing `get_task_admin` handler):

```python
class AdminTaskUpdate(BaseModel):
    domain: str | None = None
    estimated_duration: int | None = None

    @field_validator("domain")
    @classmethod
    def _valid_domain(cls, v: str | None) -> str | None:
        if v is not None and v not in {"personal", "work", "family"}:
            raise ValueError("domain must be personal, work, or family")
        return v


@router.patch("/tasks/{task_id}")
async def update_task_admin(
    request: Request,
    task_id: str,
    body: AdminTaskUpdate,
) -> dict[str, Any]:
    """Edit domain/duration from the dashboard. Fires correction learning."""
    db = request.app.state.db
    existing = await db.get_task(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Task not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"id": task_id, "domain": existing.domain,
                "estimated_duration": existing.estimated_duration}

    row = await db.update_task(task_id, source="dashboard", **updates)
    return {"id": row.id, "domain": row.domain,
            "estimated_duration": row.estimated_duration}
```

Confirm `Request`, `HTTPException`, and `Any` are already imported in this file (the GET handlers use them). If `Any` is missing, add `from typing import Any`.

- [ ] **Step 4: Run the endpoint test to verify it passes**

Run: `uv run pytest tests/api/test_tasks_update.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the UI client function**

In `donna-ui/src/api/tasks.ts`, add (after the `fetchTask` function):

```typescript
export interface TaskUpdate {
  domain?: string;
  estimated_duration?: number;
}

export async function updateTask(id: string, body: TaskUpdate): Promise<void> {
  await client.patch(`/admin/tasks/${id}`, body);
}
```

- [ ] **Step 6: Add an inline editor to the detail panel**

In `donna-ui/src/pages/Tasks/TaskDetailExpander.tsx`:

Add `updateTask` to the existing `../../api/tasks` import:

```typescript
import {
  fetchTask,
  updateTask,
  type Correction,
  type NudgeEvent,
  type Subtask,
  type TaskDetail,
  type TaskInvocation,
} from "../../api/tasks";
```

Replace the two read-only `DetailField` lines for Domain and Duration:

```tsx
          <DetailField label="Domain" value={task.domain ?? "—"} />
```
```tsx
          <DetailField label="Duration (est)" value={task.estimated_duration ?? "—"} />
```

with editable controls:

```tsx
          <EditableDomain task={task} />
          <EditableDuration task={task} />
```

Add these two components at the bottom of the file (after `DetailField`):

```tsx
function EditableDomain({ task }: { task: TaskDetail }) {
  const [value, setValue] = useState(task.domain ?? "personal");
  const [saving, setSaving] = useState(false);
  return (
    <div className={styles.field}>
      <dt className={styles.fieldLabel}>Domain</dt>
      <dd className={styles.fieldValue}>
        <select
          value={value}
          disabled={saving}
          onChange={async (e) => {
            const next = e.target.value;
            setValue(next);
            setSaving(true);
            try {
              await updateTask(task.id, { domain: next });
            } finally {
              setSaving(false);
            }
          }}
        >
          <option value="personal">personal</option>
          <option value="work">work</option>
          <option value="family">family</option>
        </select>
      </dd>
    </div>
  );
}

function EditableDuration({ task }: { task: TaskDetail }) {
  const initial = task.estimated_duration ? String(task.estimated_duration) : "";
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  return (
    <div className={styles.field}>
      <dt className={styles.fieldLabel}>Duration (est, min)</dt>
      <dd className={styles.fieldValue}>
        <input
          type="number"
          min={5}
          value={value}
          disabled={saving}
          onChange={(e) => setValue(e.target.value)}
          onBlur={async () => {
            const minutes = parseInt(value, 10);
            if (Number.isNaN(minutes) || minutes < 5) return;
            setSaving(true);
            try {
              await updateTask(task.id, { estimated_duration: minutes });
            } finally {
              setSaving(false);
            }
          }}
        />
      </dd>
    </div>
  );
}
```

`task.estimated_duration` is typed `string | null` in `tasks.ts`; `parseInt` handles both the string form and the editor input.

- [ ] **Step 7: Type-check and build the UI**

Run: `cd donna-ui && npm run build`
Expected: build succeeds with no TypeScript errors

- [ ] **Step 8: Commit**

```bash
git add src/donna/api/routes/admin_tasks.py donna-ui/src/api/tasks.ts donna-ui/src/pages/Tasks/TaskDetailExpander.tsx tests/api/test_tasks_update.py
git commit -m "feat: dashboard edit control for task domain and duration"
```

---

## Task 10: Validate local parse quality and tune

Before relying on local-first in production, confirm the local model parses well with the new prompt, and tune the anchors/threshold from real output. This is a verification task — no code unless the eval surfaces a regression.

**Files:**
- Create: `tests/fixtures/parse_eval_cases.jsonl` (labeled cases) — only if the eval harness needs an input set; check the harness first.

- [ ] **Step 1: Inspect the eval harness invocation**

Run: `uv run donna eval --help`
Expected: usage text showing `--task-type` and how labeled cases are supplied. Note the expected input format.

- [ ] **Step 2: Assemble a small labeled set**

Create ~15 cases covering the failure modes, in the format the harness expects (from Step 1). Include at minimum:
- "send Sarah an email about the budget" → domain work, ~15 min
- "call the dentist to schedule a cleaning" → domain personal, ~15 min
- "touch base with Mike" → ambiguous, expect confidence < 0.7
- "write the Q3 board deck" → domain work, > 60 min
- "buy groceries" → domain personal, ~30 min

- [ ] **Step 3: Run the local eval**

Run: `uv run donna eval --task-type parse_task_local`
Expected: a per-case report of predicted vs expected domain/duration and confidence.

- [ ] **Step 4: Tune from results**

- If durations still skew high, tighten the anchor wording in `prompts/parse_task.md` (Task 2) and re-run.
- If too many/few cases escalate, adjust `confidence_threshold` in `config/donna_models.yaml` (Task 6).
- Re-run Step 3 until domain accuracy and duration error are acceptable.

- [ ] **Step 5: Commit any tuning changes**

```bash
git add prompts/parse_task.md config/donna_models.yaml tests/fixtures/parse_eval_cases.jsonl
git commit -m "chore: tune parse anchors and escalation threshold from local eval"
```

---

## Final verification (CI gates)

Per project convention, run the CI gates before opening a PR:

- [ ] Run full type check: `uv run mypy src/`
- [ ] Run full lint: `uv run ruff check .`
- [ ] Run the affected tests: `uv run pytest tests/unit/test_input_parser.py tests/unit/test_task_context.py tests/unit/test_router_confidence.py tests/api/test_tasks_update.py -v`
- [ ] Build the UI: `cd donna-ui && npm run build`
