# Skill System Wave 1 — Production Enablement

**Status:** Draft
**Author:** Nick (with brainstorming assistance from Claude)
**Date:** 2026-04-16
**Scope:** Medium. Four follow-up items bundled into one wave. 3–5 days of focused work plus test authorship.
**Predecessor:** `2026-04-15-skill-system-and-challenger-refactor-design.md`, followups inventory at `docs/superpowers/followups/2026-04-16-skill-system-followups.md`.

---

## 1. Overview

The skill system shipped through Phases 1–5 in the predecessor spec. Three stubs and one missing test block flipping `skill_system.enabled=true` in production:

1. **`AutoDrafter` and `Evolver` accept `executor_factory=None`.** Gates 2/3/4 of evolution validation and auto-drafter fixture validation return `pass_rate=1.0` vacuously. Draft and evolution safety depends on human approval alone; the automated validation layer is a stub.
2. **`NotificationService` is never instantiated** in either the orchestrator (`src/donna/cli.py`) or the API (`src/donna/api/__init__.py`) process. `AutomationDispatcher` defensively skips notifications when `self._notifier is None`, so alerts never go out.
3. **No end-to-end smoke test** exercises the full lifespan with `enabled=true`. Wiring drift across five phases of implementation would only surface in production.

This wave addresses all three by shipping a real `ValidationExecutor`, moving the automation scheduler into the orchestrator process where `DonnaBot` and `NotificationService` naturally live, and adding a mocked-LLM FastAPI + orchestrator smoke test.

Wave 1 is **prerequisite infrastructure**, not user-visible feature work. After Wave 1, `skill_system.enabled=true` becomes a safe config toggle. User value accumulates in Wave 2 (seeding real capabilities + Discord automation creation).

---

## 2. Out of Scope

| # | What | Why deferred | When to reconsider |
|---|---|---|---|
| OOS-W1-1 | Real-LLM E2E smoke test variant (pytest `e2e_full` marker calling real Ollama + Claude) | F-14 uses mocked LLMs for CI-friendliness and determinism. A real-LLM variant is useful but adds runtime cost and GPU dependency; not required for production enablement. | When a mocked-LLM regression escapes to production. |
| OOS-W1-2 | `AutomationDispatcher.skill_executor_factory=lambda: None` — production execution of skill-path automations | Different factory from F-1 (this is the production `SkillExecutor`, not the `ValidationExecutor`). Currently nothing routes through it because no `product_watch` / `news_check` / `meeting_prep` skills exist. Pair this with Wave 2 F-11 when seed skills land. | Wave 2 (F-11 seed capabilities). |
| OOS-W1-3 | Move evolution scheduler, auto-drafter, correction-cluster detector to orchestrator process | Only automation scheduler moves in F-6 because it's the one that needs `NotificationService`. Moving the rest of the skill-system cron work is a larger refactor and isn't blocking production enablement. | Wave 3 if the split causes operational friction. |
| OOS-W1-4 | Dashboard UI for manual validation-run-now, fixture editing, validation report browsing | F-4 Dashboard is a separate project track. Wave 1 exposes validation results via the existing `skill_evolution_log.validation_results` JSON blob and logs; dashboard rendering comes later. | Wave 2/3 (F-4). |
| OOS-W1-5 | Semantic equivalence in fixture validation (Claude-judge on final output) | Wave 1's validation is structural (schema-valid). Semantic judging costs Claude tokens per fixture run and duplicates the shadow-agreement mechanism already running in lifecycle state `sandbox` onward. | If structural validation misses quality regressions at Gate 4 in practice. |
| OOS-W1-6 | Retry/replay of failed notifications queued by orchestrator | Blackout/quiet-hours queueing is already in `NotificationService`. Failed-send retry (Discord API down) is not; the dispatcher logs and moves on. | When first production outage proves it matters. |
| OOS-W1-7 | Full `PendingNotification` DB queue | Option A from brainstorming — rejected in favor of Option B (process move). Not revisiting unless Wave 3+ reveals the process move was wrong. | Never under current architecture. |

---

## 3. Core Concepts and Vocabulary

Wave 1 introduces few new terms, but clarifies one naming collision from the predecessor spec.

**ValidationExecutor.** A new offline executor class that implements the existing `executor.execute(skill, version, inputs, user_id)` protocol (compatible with `validate_against_fixtures` in `src/donna/skills/fixtures.py`). Internally wires up a real `SkillExecutor` per call with a `MockToolRegistry` + a `ValidationRunSink` so fixture validation runs against real local Ollama but against mocked tools and without writing to production tables. Used exclusively by `AutoDrafter` fixture validation and `Evolver` gates 2/3/4.

**Lifecycle-state `sandbox` vs. validation "sandbox".** The predecessor spec used the word "sandbox" for both (a) a lifecycle state where a skill runs in shadow mode against real traffic using the real `SkillExecutor` + real tools, and (b) an offline validation environment used before a skill reaches any lifecycle state. Wave 1 reserves the word "sandbox" for the lifecycle state (a) and uses "validation" for (b). The class in F-1 is named `ValidationExecutor`, not `SandboxExecutor`.

**`FixtureValidationReport` (existing).** Already defined in `src/donna/skills/fixtures.py`. Contains `total`, `passed`, `failed`, `pass_rate`, and `failure_details: list[FixtureFailureDetail]`. Wave 1 does not introduce a parallel type — AutoDrafter's and Evolver's pass/fail decisions continue to read `report.pass_rate`. Evolver's `skill_evolution_log.validation_results` stores a JSON serialization of this existing dataclass.

**`FixtureFailureDetail` (existing).** One element of `FixtureValidationReport.failure_details`: `{case_name: str, reason: str}`. Wave 1 does not change the shape; failure reasons produced by `ValidationExecutor` (timeout, unmocked tool, schema mismatch) fit into `reason` as strings.

**Tool mock.** A pre-computed tool invocation result keyed on a stable invocation fingerprint (tool name + normalized args). Stored in the new `skill_fixture.tool_mocks` JSON column. Validation runs read tool results from here instead of dispatching real tools.

**Pending-run nudge (not a new concept).** Existing `automation.next_run_at` column. The admin "run now" endpoint in the API sets it to the current timestamp; the orchestrator's scheduler picks up the row on its next poll. This replaces the direct `dispatcher.dispatch()` call that lived in the API admin route.

---

## 4. Architecture

### 4.1 Process split (after Wave 1)

| Process | Owns |
|---|---|
| `donna-orchestrator` (port 8100) | `DonnaBot` (Discord connection). `NotificationService`. `AutomationScheduler` + `AutomationDispatcher` (moved from API). `AsyncCronScheduler` for nightly skill work (auto-drafter, evolver, correction-cluster detector). Skill system `assemble_skill_system` bundle. `ValidationExecutor` factory (used inside auto-drafter + evolver). |
| `donna-api` (port 8200) | FastAPI REST routes for the dashboard — capabilities, skills, skill runs, skill drafts, automations, admin health. CRUD only; no background schedulers. Reads `app.state.skill_system_config` for `enabled` gate but does not wire background tasks. |

Both processes open the same `donna_tasks.db` (aiosqlite, WAL mode). The database is the IPC mechanism for everything the API needs to tell the orchestrator (for example, "run this automation now" = `UPDATE automation SET next_run_at = datetime('now')`).

### 4.2 Flow: a fixture validation run

```
AutoDrafter / Evolver
    │
    ▼
ValidationExecutorFactory()  ──▶  ValidationExecutor
                                       │
                        ┌──────────────┼──────────────┐
                        ▼              ▼              ▼
                   MockToolRegistry  ModelRouter  ValidationRunSink
                   (deny-closed,     (real        (in-memory only;
                   reads from        Ollama)      no DB writes)
                   fixture.tool_mocks)
                        │
                        ▼
                  SkillExecutor.execute(skill_version, fixture.input)
                        │
                        ▼
                  per-step output → schema validation (existing Phase 2 code)
                        │
                        ▼
                  final_output → schema validation vs. fixture.expected_output_shape
                  (this step stays inside validate_against_fixtures, unchanged)
                        │
                        ▼
                  FixtureFailureDetail{case_name, reason}  on failure
                        │
                        ▼  (aggregated across all fixtures by validate_against_fixtures)
                  FixtureValidationReport{total, passed, failed, pass_rate, failure_details}
```

The `ValidationExecutor` wraps — it does not replace — the existing `SkillExecutor` from Phase 2. Its job is to construct the right dependencies, hand them to a normal `SkillExecutor` instance per fixture, and aggregate results.

### 4.3 Flow: an automation dispatch (after F-6 move)

```
                    donna-orchestrator
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  AutomationScheduler (every N seconds, default 60)           │
│      │                                                       │
│      ▼                                                       │
│  AutomationRepository.list_due(now) ──▶ [Automation, ...]    │
│      │                                                       │
│      ▼                                                       │
│  AutomationDispatcher.dispatch(automation)                   │
│      │                                                       │
│      ├── skill state ≥ shadow_primary? → SkillExecutor (stub │
│      │   factory still returns None; covered by OOS-W1-2)    │
│      │                                                       │
│      └── else → claude_native path (real Claude)             │
│           │                                                  │
│           ▼                                                  │
│      AlertEvaluator.check(output, automation.alert_conditions)│
│           │                                                  │
│           ▼                                                  │
│      NotificationService.dispatch(                           │
│          type=NOTIF_AUTOMATION_ALERT,                        │
│          content=rendered_alert,                             │
│          channel=CHANNEL_TASKS)                              │
│           │                                                  │
│           ▼                                                  │
│      DonnaBot.send_message(...)  ──▶  Discord                │
│                                                              │
└──────────────────────────────────────────────────────────────┘

                    donna-api
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  POST /admin/automations/{id}/run-now                        │
│      │                                                       │
│      ▼                                                       │
│  UPDATE automation SET next_run_at = datetime('now')         │
│  WHERE id = ?                                                │
│      │                                                       │
│      ▼                                                       │
│  return 202 Accepted                                         │
│      (orchestrator picks up within scheduler poll interval)  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

User replies in Discord (to a threaded notification) hit `DonnaBot.on_message` in the orchestrator — same process as the dispatcher that created the thread — and can look up `automation_run.discord_thread_id` to route to the appropriate reply handler. Cross-process coordination is not needed for reply flows.

### 4.4 Flow-through invariants

Inherited from the predecessor spec §4.2, plus two new Wave 1 invariants:

7. **Validation writes no production state.** `ValidationExecutor` runs must never touch `skill_run`, `skill_step_result`, `invocation_log`, or any production table. All validation results are returned in-memory to the caller; Evolver persists the JSON blob to `skill_evolution_log.validation_results`, AutoDrafter uses the result for a pass/fail decision only.

8. **Tool dispatch in validation is deny-closed.** The `MockToolRegistry` exposes only mocked tool implementations. Any tool invocation that cannot be resolved from `fixture.tool_mocks` raises `UnmockedToolError` and the fixture run is marked failed. No real tool callable is ever registered with a `MockToolRegistry`.

### 4.5 Component interactions

The components that change in Wave 1 relative to the predecessor spec:

| Component | Before Wave 1 | After Wave 1 |
|---|---|---|
| `src/donna/skills/startup_wiring.py` (`assemble_skill_system`) | Accepts `executor_factory=None`. Wires `AutoDrafter` and `Evolver` with that None, producing vacuous validation passes. | Accepts `validation_executor_factory` (renamed for clarity, callable returning a `ValidationExecutor`). Defaults to a factory that constructs a real `ValidationExecutor` using the current `model_router` and a fresh `MockToolRegistry`. |
| `src/donna/api/__init__.py` lifespan | Constructs `AutomationScheduler` + `AutomationDispatcher`. Passes `notifier=getattr(app.state, "notification_service", None)`, which is always `None`. | Does **not** construct automation scheduler or dispatcher. Keeps admin REST routes. Registers the skill-system bundle and config only for read-only admin endpoint use. |
| `src/donna/cli.py` (orchestrator entrypoint) | Starts `DonnaBot`, logs but does not instantiate `NotificationService`. No automation wiring. | Instantiates `NotificationService(bot, calendar_config, user_id)` and attaches it. Constructs `AutomationScheduler` + `AutomationDispatcher` with the real `NotificationService`. Starts the cron scheduler tasks currently in the API lifespan (moves to orchestrator). |
| `src/donna/skills/auto_drafter.py` | Calls `self._executor_factory()` if set, else returns `pass_rate=1.0`. | `validation_executor_factory` is always set (no None fallback). Validation always runs. Vacuous-pass code path removed. |
| `src/donna/skills/evolution.py` (`Evolver`) | Same None-fallback pattern for gates 2/3/4. | Always has a real executor factory. Gates produce real pass rates from `FixtureValidationReport`. |
| `src/donna/automations/dispatcher.py` | Reads `self._notifier` from the passed-in `notification_service` (None in practice). | No code change — it was already correct. The wiring change is above (API → orchestrator). |
| `src/donna/api/routes/automations.py` `POST /admin/automations/{id}/run-now` | Directly calls dispatcher. | Sets `next_run_at = now()` on the row and returns `202 Accepted`. |

### 4.6 New directories and files

```
src/donna/skills/
  validation_executor.py       # ValidationExecutor (SkillExecutor-compatible)
  mock_tool_registry.py        # MockToolRegistry, UnmockedToolError
  validation_run_sink.py       # In-memory SkillRun/SkillStepResult sink (no DB writes)

alembic/versions/
  <revision>_add_fixture_tool_mocks.py   # Adds skill_fixture.tool_mocks column

tests/e2e/
  __init__.py
  conftest.py                  # Shared harness fixtures (mocked Ollama, throwaway DB)
  test_wave1_smoke.py          # Four scenarios from §7.4
```

No files moved in `src/donna/automations/` — only the wiring changes. The classes themselves are fine where they are.

---

## 5. Data Model

### 5.1 `skill_fixture.tool_mocks` column

```
tool_mocks            TEXT  -- JSON: { "<invocation_fingerprint>": <result_blob>, ... }
```

**Fingerprint format.** Stable string of `tool_name + ":" + canonical_json(normalized_args)`. Normalization strips variable-only fields (timestamps in request headers, dynamic tokens) defined per-tool in a small registry. Rendering is pure-Python and deterministic; the same (tool, args) always produces the same fingerprint.

**Result blob format.** Opaque JSON — whatever the tool would have returned. For `web_fetch`, this is `{"status": 200, "body": "<html>", "headers": {...}}`; for `gmail_read`, it's the message dict. Matches the tool's real return shape exactly so the `SkillExecutor` downstream doesn't know it was mocked.

**Population rules.**

| Fixture source | How `tool_mocks` is populated |
|---|---|
| `captured_from_run` | Auto-generated from the captured `skill_run.tool_result_cache` at fixture-creation time. Migration backfills existing captured fixtures. |
| `claude_generated` (from AutoDrafter / Evolver) | Claude's fixture-generation prompt is updated to emit a `tool_mocks` field alongside `input` and `expected_output_shape`. The fixture-creation code pulls this field through. |
| `human_written` | Authored inline in the fixture JSON. Dashboard editor (Wave 3+) validates required mocks against the skill's tool invocations; Wave 1 accepts whatever is written. |

**Nullability.** `tool_mocks` is nullable. A fixture may legitimately have no tool invocations (pure-LLM skills). `MockToolRegistry` treats absent/null as an empty map.

**Migration.** Alembic revision adds the column; backfill updates existing `skill_fixture` rows where `source='captured_from_run'` by reading the corresponding `skill_run.tool_result_cache` and rewriting it in fingerprint-keyed form.

### 5.2 `expected_output_shape` convention (documented, not schema-changing)

`expected_output_shape` on `skill_fixture` is a **structural** JSON Schema — validates field names, types, required fields, nested structure. It does NOT pin exact values except in legitimate enum cases (`{"status": {"enum": ["in_stock", "sold_out"]}}`).

- Captured-run fixtures: schema auto-generated from the captured `skill_run.final_output` using `donna.skills.schema_inference.json_to_schema()` (new helper, ~30 lines).
- Claude-generated fixtures: AutoDrafter / Evolver fixture-generation prompts are updated with the rule. The Wave 1 plan includes a prompt diff.
- Human-written fixtures: dashboard lint in a future wave warns on over-specified schemas. Wave 1 assumes human judgment.

### 5.3 No other schema changes

F-5 (wire-up) is Python-only. F-6 (process move) is wiring-only. F-14 (E2E test) uses existing tables.

---

## 6. Subsystem Designs

### 6.1 F-1 ValidationExecutor

**Purpose.** Provide a drop-in executor that conforms to the existing `executor.execute(skill, version, inputs, user_id)` protocol used by `validate_against_fixtures(...)` in `src/donna/skills/fixtures.py`, but which uses mocked tools + non-persisting state so that fixture validation never touches production tables or dispatches real tools.

The existing `validate_against_fixtures(...)` helper and `FixtureValidationReport` dataclass stay as-is. The existing `Fixture` dataclass (`case_name`, `input`, `expected_output_shape`) gains one new field: `tool_mocks: dict | None`. AutoDrafter and Evolver continue to call `validate_against_fixtures(skill, executor, fixtures, version)` as today — they just get a real executor instead of `None`.

**Public interface.**

```python
class ValidationExecutor:
    """SkillExecutor-compatible class for offline fixture validation.

    Matches the `executor.execute(skill, version, inputs, user_id)` shape
    so that `validate_against_fixtures(...)` accepts it unchanged.
    """

    def __init__(
        self,
        model_router: ModelRouter,
        config: SkillSystemConfig,
        *,
        per_step_timeout_s: int | None = None,
        per_run_timeout_s: int | None = None,
    ) -> None: ...

    async def execute(
        self,
        *,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
        tool_mocks: dict | None = None,
    ) -> SkillRunResult: ...
```

**Tool mock plumbing.** `validate_against_fixtures` is updated to pass `fixture.tool_mocks` through to `executor.execute(..., tool_mocks=...)`. Real `SkillExecutor.execute` will accept and ignore the parameter (or not receive it at all — parameter is keyword-only on `ValidationExecutor`). The existing `executor.execute(...)` call sites in production code stay unchanged.

**Algorithm inside `ValidationExecutor.execute`.**

```
tool_registry = MockToolRegistry.from_mocks(tool_mocks or {})
sink = ValidationRunSink()
inner = SkillExecutor(
    model_router=self._model_router,
    tool_registry=tool_registry,
    run_sink=sink,
    model_alias_override="local_parser_validation",
)
with timeout(self._per_run_timeout_s):
    return await inner.execute(
        skill=skill, version=version,
        inputs=inputs, user_id=user_id,
    )
# TimeoutError, UnmockedToolError, and other exceptions propagate to
# validate_against_fixtures, which already wraps them into FixtureFailureDetail.
```

**Dependency injection on `SkillExecutor`.** The existing `SkillExecutor` from Phase 2 already accepts a `tool_registry`. Wave 1 extends the `SkillExecutor` constructor with an optional `run_sink` parameter (default: the existing persisting behavior). When `run_sink` is provided, the executor delegates `skill_run` / `skill_step_result` / `invocation_log` writes to it instead of writing directly. `ValidationRunSink` is a no-op implementation that captures the writes in memory and discards them. Production callers don't pass `run_sink`, so behavior is unchanged.

**Pass rule consolidation.** `validate_against_fixtures` already implements the "schema-valid final output AND matches `expected_output_shape`" rule (lines 84–96 of `src/donna/skills/fixtures.py`). Wave 1 preserves this rule — no change. The convention in §5.2 (structural schemas, not pinned values) is what makes this safe.

**Model alias.** All LLM calls during validation are tagged with task_type `skill_validation::<capability>::<step>` and model_alias `local_parser_validation`. This keeps validation invocations filterable in `invocation_log`. Production metrics (e.g., daily cost reports) exclude this alias.

**Timeouts.** Per-step and per-run configurable via `config/skills.yaml` (extends the existing `SkillSystemConfig`). Defaults: 60s per step, 300s per full skill run.

**Parallelism.** Sequential fixture execution. `for fixture in fixtures` — no asyncio.gather. Rationale: Ollama single-GPU queueing makes parallelism deliver marginal speedup at significant debugging cost; sequential keeps nightly cron duration predictable.

**Persistence.** None. `ValidationRunSink` absorbs the `SkillRun`, `SkillStepResult`, and `InvocationLog` writes that `SkillExecutor` would otherwise make and retains them in-memory for the duration of the fixture run, then is discarded. The existing `FixtureValidationReport` / `FixtureFailureDetail` types capture the outcome; detailed per-step traces are available via structlog events emitted by the `SkillExecutor` but are not persisted.

### 6.2 F-1 MockToolRegistry

**Purpose.** Drop-in replacement for the real `ToolRegistry` that services tool invocations from a fixture's `tool_mocks` blob instead of dispatching real callables.

**Public interface.**

```python
class MockToolRegistry(ToolRegistry):
    @classmethod
    def from_fixture(cls, fixture: SkillFixture) -> MockToolRegistry: ...

    async def dispatch(self, tool_name: str, args: dict) -> Any:
        fingerprint = self._fingerprint(tool_name, args)
        if fingerprint not in self._mocks:
            raise UnmockedToolError(tool_name=tool_name, fingerprint=fingerprint)
        return self._mocks[fingerprint]
```

**Fingerprinting.** Per-tool normalization rules defined in a small registry (`src/donna/skills/tool_fingerprint.py`). For `web_fetch(url, timeout_s=..., headers=...)`, the fingerprint uses only `url` (timeout and headers don't change the response); for `gmail_read(message_id)`, just `message_id`. Fingerprint rules live next to the tool definitions for discoverability.

**Unmocked tool behavior.** `UnmockedToolError` is caught by the `ValidationExecutor`'s fixture loop, which marks the fixture failed with a clear reason. This surfaces gaps in Claude-generated or human-written fixture mocks — the fixture author forgot to mock a tool the skill actually invokes.

**Security posture.** The `MockToolRegistry` never registers real tool callables. Defense in depth: even if `SkillExecutor` were to directly import a real tool bypassing the registry, the isolation wouldn't hold — but skills are declarative YAML dispatched through the registry exclusively, so the registry is the actual boundary.

### 6.3 F-5 Wiring

**In `src/donna/skills/startup_wiring.py`:**

1. Rename parameter `executor_factory` → `validation_executor_factory`. Typed as `Callable[[], ValidationExecutor]` — **required**, no `None` default. All callers update to pass a real factory. The vacuous-pass behavior in AutoDrafter and Evolver is deleted in the same commit, so there is no `None` path left to support.
2. `assemble_skill_system` constructs and passes a default factory when the caller does not override:

    ```python
    def _default_validation_executor_factory() -> ValidationExecutor:
        return ValidationExecutor(
            model_router=model_router,
            config=config,
        )
    ```
3. Pass the factory through to both `AutoDrafter` and `Evolver` constructors. Update their constructors to require the factory (remove `executor_factory=None` defaults).
4. Tests that previously relied on the `None` fallback are rewritten to pass a test-double `ValidationExecutor` (or a factory returning one). No test depends on vacuous-pass behavior.

**In `src/donna/cli.py` (orchestrator lifespan):** after wiring `model_router` and `skill_config`, construct a `ValidationExecutor` factory and pass it to `assemble_skill_system(...)`.

**In `src/donna/api/__init__.py` (API lifespan):** delete the `executor_factory=None` kwarg entirely. After F-6, the API no longer calls `assemble_skill_system` — the orchestrator does.

### 6.4 F-6 Process migration

**Files changed in `src/donna/cli.py`:**

1. Load `CalendarConfig` via the existing `load_calendar_config(config_dir)` helper in `src/donna/config.py`.
2. Instantiate `NotificationService(bot, calendar_config, user_id)` after `DonnaBot` is constructed but before the Discord start task is awaited. Hold it as a local variable `notification_service` in the `run_async` scope. The orchestrator doesn't have a FastAPI-like `app.state`, so the usual pattern is passing dependencies directly into constructors (see how `db` and `router` are already threaded through). Wave 1 follows that pattern — no new "state object" abstraction.
3. Move the skill-system wiring block (currently lines 196–330 of `src/donna/api/__init__.py`) to the orchestrator entrypoint. Preserves all existing behavior — just in a different process.
4. Construct `AutomationRepository`, `AutomationDispatcher`, `AutomationScheduler`. Pass the real `NotificationService` as the dispatcher's `notifier` (this matches `AutomationDispatcher`'s existing `.dispatch(...)` call pattern). Pass the `ValidationExecutor` factory to `assemble_skill_system`.
5. Start the scheduler and nightly-cron background tasks. The existing `assemble_skill_system` also takes a `notifier: Callable[[str], Awaitable[None]]` — a **separate, plain-string notifier** used by `CorrectionClusterDetector`. Wave 1 provides an adapter: a small function that wraps `notification_service.dispatch(...)` with `notification_type=NOTIF_AUTOMATION_FAILURE` (closest existing type for "skill system warning"; a dedicated `NOTIF_SKILL_DEGRADED` constant can land later in Wave 3 without breaking anything). The adapter lives inline in `cli.py` — no new file.

    ```python
    async def _skill_system_notifier(message: str) -> None:
        await notification_service.dispatch(
            notification_type=NOTIF_AUTOMATION_FAILURE,
            content=message,
            channel=CHANNEL_TASKS,
            priority=4,
        )
    ```

    The two-interface split (plain-callable notifier for skill system cron, rich `.dispatch()` for automation dispatcher) is preserved in Wave 1 rather than refactored, because the refactor has nothing to do with production enablement and would enlarge blast radius.

**Files changed in `src/donna/api/__init__.py`:**

1. Delete the skill-system background-task wiring block. Keep `skill_system_config` loading for admin routes that need to report `enabled` status.
2. Delete automation scheduler/dispatcher construction.
3. Admin routes in `src/donna/api/routes/automations.py` that currently call `app.state.automation_dispatcher.dispatch(...)` change to DB-level operations.

**`POST /admin/automations/{id}/run-now` change:**

```python
@router.post("/automations/{automation_id}/run-now", status_code=202)
async def run_now(automation_id: str, db: Database = Depends(get_db)) -> dict:
    now = datetime.now(tz=timezone.utc).isoformat()
    cursor = await db.connection.execute(
        "UPDATE automation SET next_run_at = ? WHERE id = ? AND status = 'active'",
        (now, automation_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "automation not found or inactive")
    await db.connection.commit()
    return {"status": "scheduled", "next_run_at": now}
```

The scheduler in the orchestrator picks this up on the next poll (≤60s by default; adjustable in `config/skills.yaml`). Response body documents the behavior so dashboard UI can communicate "running within a minute" instead of expecting instant completion.

**Config addition.** `config/skills.yaml` gains `automation_run_now_poll_interval_seconds` — short poll interval used after a manual run-now so the response feels snappy. Implementation: scheduler uses a shorter interval (default 5s) when any automation has `next_run_at < now + 5s`, reverts to the normal interval (default 60s) otherwise. This is a small polish item; if it adds complexity, a constant 15s interval is acceptable.

**NotificationService construction.**

```python
from donna.config import load_calendar_config
from donna.notifications.service import NotificationService

calendar_config = load_calendar_config(config_dir)
notification_service = NotificationService(
    bot=bot,
    calendar_config=calendar_config,
    user_id=user_id,
    sms=None,   # Wave 1 scope: Discord only. SMS/Gmail wiring deferred.
    gmail=None,
)
```

**Smoke manual-test.** After F-6, starting `donna-orchestrator` locally with the Discord token should:
1. Log `notification_service_wired`.
2. Allow manual trigger via `python -m donna.cli test-notification --type digest --channel tasks --content "hello from Wave 1"` (new dev-only CLI command; thin shim over `notification_service.dispatch`).
3. Deliver the message to the Discord tasks channel.

**Docker compose.** No compose file changes needed — the orchestrator container already has DB, prompts, schemas, and config mounted. No new environment variables.

### 6.5 F-14 E2E smoke test

**Goal.** Detect wiring-level breakage in the full `enabled=true` pipeline across one CI run. Four scenarios chosen because each exercises a different set of components that drift independently.

**Test harness.**

- **DB.** Throwaway `tmp_path / "donna.db"` with the full Alembic migration chain applied.
- **Ollama mock.** A fake `OllamaProvider` subclass with `canned_responses: dict[str, dict]` keyed on `task_type`. Returns the canned structured output for that task type; records invocation for assertion.
- **Claude mock.** Existing test pattern (see `tests/integration/test_*_claude.py`) — a fake Claude client returning canned structured outputs.
- **DonnaBot mock.** A minimal `FakeDonnaBot` that records `send_message` calls without touching Discord. `NotificationService` accepts any object that implements the bot interface, so no code change in `NotificationService`.
- **Calendar config.** Loaded with blackout hours set to none so tests don't get queued.
- **Clock.** `freezegun` for scheduled time assertions.

**Scenarios.**

| # | Name | Asserts |
|---|---|---|
| 1 | Nightly cycle produces draft | Seed 200 invocations of a claude_native task type with known cost. Trigger `run_nightly_tasks()`. Expect: `skill_candidate_report` row with `status='drafted'`, `skill_version` row with `state='draft'`, `skill_state_transition` audit row. |
| 2 | Automation tick produces run + alert | Seed an active automation with `next_run_at` in the past, simple `alert_conditions`, and a mock output that fires the condition. Run `scheduler.run_once()`. Expect: `automation_run` row with `status='succeeded'`, `alert_sent=1`, `FakeDonnaBot.send_message` called once with the rendered alert content. |
| 3 | Sandbox → shadow_primary auto-promotion | Seed a skill in `sandbox` with `requires_human_gate=0`. Insert 20 `skill_run` rows with `status='succeeded'` and schema-valid `final_output`. Call `SkillLifecycleManager.evaluate_auto_promotions()` (new helper if not already exposed). Expect: skill state = `shadow_primary`, one `skill_state_transition` row with `reason='gate_passed'`. |
| 4 | Trusted → flagged_for_review degradation | Seed a trusted skill with `baseline_agreement=0.90`. Insert 30 `skill_divergence` rows with `overall_agreement=0.65`. Run `DegradationDetector.check_all_trusted_skills()`. Expect: skill state = `flagged_for_review`, one transition row. Note: `DegradationDetector` does not currently dispatch a notification on flagging — the EOD digest is the existing mechanism (see `src/donna/notifications/eod_digest.py`). The scenario asserts the transition only; notification assertion belongs to a separate EOD-digest test path. |

**Execution.** Each scenario instantiates a minimal orchestrator runtime (not the full `cli.py` — a test-only helper `tests/e2e/harness.py` that mirrors the production wiring). The harness is itself tested in isolation via an integration test that asserts it produces the same components `cli.py` does.

**CI behavior.** `pytest tests/e2e/ -v`. Runs on every commit. Expected duration: ≤30s for all four scenarios (no real Ollama, no real Claude, no real DB beyond SQLite).

**Real-LLM variant.** OOS-W1-1. Not part of Wave 1.

---

## 7. Phased Rollout

Wave 1 is small enough for a single implementation phase with tight internal ordering. No need for the multi-phase structure of the predecessor spec.

### Implementation order (must be sequential)

1. **Migration: `skill_fixture.tool_mocks` column.** Alembic revision + backfill for `source='captured_from_run'` fixtures. Test: migration up/down on empty and populated DBs.
2. **`MockToolRegistry` + `UnmockedToolError`.** Standalone unit-testable class with fingerprinting rules for `web_fetch`, `gmail_read`, `gmail_send` (email is draft-only but we mock the draft-creation return). Test: fingerprint stability, missing-mock path, empty-mocks path.
3. **`ValidationRunSink`.** In-memory `SkillRun` / `SkillStepResult` accumulator. Test: `SkillExecutor` wired with the sink produces no DB writes.
4. **`ValidationExecutor`.** Composes the above. Test: run a two-fixture suite against a hand-written two-step skill; assert pass_rate, per-fixture results, that no `skill_run` rows exist afterward, and that per-step timeout triggers a fixture failure rather than hanging.
5. **Wire F-5.** Update `assemble_skill_system` signature, default factory. `AutoDrafter` and `Evolver` vacuous-pass paths deleted. Unit tests for AutoDrafter and Evolver that previously asserted `pass_rate=1.0` on the no-executor path update to assert real validation results.
6. **F-6 process move.** Sub-steps:
    - 6a. Construct `NotificationService` in `cli.py`. No dispatcher hookup yet. Ship the `test-notification` dev CLI. Manually verify Discord delivery.
    - 6b. Move skill-system wiring block from API lifespan to orchestrator lifespan. API tests that assert `app.state.skill_system_bundle` is non-null change to assert None (or skip if the admin route shouldn't depend on it).
    - 6c. Move automation scheduler + dispatcher. Dispatcher's `notifier` is now the real `NotificationService`.
    - 6d. Change `POST /admin/automations/{id}/run-now` to set `next_run_at=now()` and return 202. Update API tests accordingly.
7. **F-14 E2E smoke test.** Harness + four scenarios. Runs green.
8. **Documentation updates.** `docs/architecture.md` to reflect the orchestrator-owned scheduler. `docs/notifications.md` to note `NotificationService` is now live. `docs/superpowers/followups/...md` to tick F-1/F-5/F-6/F-14 off the backlog.

### Handoff contract (Wave 1 → Wave 2)

After Wave 1 merges, the following must be true:

- `skill_system.enabled=true` is safe to flip in `config/skills.yaml` production. All four production-enablement criteria from §1 hold.
- `src/donna/cli.py` owns all skill-system and automation background tasks. `src/donna/api/__init__.py` is REST-only.
- `skill_fixture.tool_mocks` exists and is populated for all `captured_from_run` fixtures.
- `assemble_skill_system(...)` accepts `validation_executor_factory` (real default). The `None` fallback and `pass_rate=1.0` vacuous-pass code paths are deleted.
- `ValidationExecutor`, `MockToolRegistry`, `ValidationRunSink` are the only classes introduced in `src/donna/skills/` under Wave 1.
- `NotificationService` is instantiated in the orchestrator and reachable via an orchestrator-scoped state object. The `app.state.notification_service` attribute in the API process is gone.
- `POST /admin/automations/{id}/run-now` returns 202 and sets `next_run_at=now()`.
- `tests/e2e/test_wave1_smoke.py` is green on CI.

### Acceptance scenarios (for verification-before-completion)

- **AS-W1.1 — Validation produces a real pass rate.** In a test DB: seed a skill version with a step whose output schema is intentionally violated by the mocked LLM response. Run `AutoDrafter.validate_draft(...)` with a two-fixture suite. Expect: `pass_rate < 1.0`, specific fixture marked failed with `failure_reason` mentioning schema.
- **AS-W1.2 — Gate 3 rejects a regressive version.** Start with a trusted skill. Construct a new version that changes a step prompt to always return `{"escalate": {"reason": "test"}}`. Run `Evolver._run_gates(...)` against the existing fixture library. Expect: Gate 3 pass rate < 95%, outcome `rejected_validation`.
- **AS-W1.3 — Automation alert reaches Discord (manual).** Start orchestrator locally with real Discord token and test channel. Create an automation via dashboard with alert condition `{"field": "ok", "op": "==", "value": true}`. POST `/admin/automations/{id}/run-now`. Within 1 minute: Discord message appears in the configured channel.
- **AS-W1.4 — `run-now` returns 202, not 200.** API test hits the endpoint; asserts status 202 and response body includes `next_run_at`.
- **AS-W1.5 — E2E smoke passes.** `pytest tests/e2e/test_wave1_smoke.py -v` → 4 passed in under 30s on the CI runner spec.
- **AS-W1.6 — Existing tests don't regress.** Full `pytest` run green. Includes migrations, all Phase 1–5 unit + integration tests.

---

## 8. Drift Log

*(Initially empty. Append entries as implementation deviates from the spec.)*

Format: see predecessor spec §8.

---

## 9. Requirements Checklist

Legend: `[x]` = done · `[~]` = partial — see drift log · `[ ]` = not yet started

| # | Requirement | Section | Verified by | ✓ |
|---|---|---|---|---|
| W1-R1 | `skill_fixture.tool_mocks` column exists with JSON shape documented in §5.1 | 5.1 | Migration test + backfill test | [x] |
| W1-R2 | `MockToolRegistry` dispatches only from `tool_mocks`; raises `UnmockedToolError` for unmocked invocations | 6.2 | Unit test `test_mock_tool_registry` | [x] |
| W1-R3 | `ValidationExecutor.execute` drops into `validate_against_fixtures(...)` unchanged and produces a real `FixtureValidationReport` | 6.1 | Unit test `test_validation_executor` | [x] |
| W1-R4 | Validation runs never write to `skill_run`, `skill_step_result`, or `invocation_log` | 4.4, 6.1 | Unit test `test_validation_no_production_writes` | [x] |
| W1-R5 | Validation LLM calls are tagged with `local_parser_validation` alias / `skill_validation::*` task_type prefix so they're filterable — even though sink suppresses invocation_log writes, the sink captures the attempted alias for the test | 6.1 | Unit test asserting the sink records the alias and no invocation_log row is persisted | [x] |
| W1-R6 | `AutoDrafter._run_sandbox_validation` consumes a real `FixtureValidationReport`, no vacuous `1.0` path | 6.3 | Unit test asserting previously-vacuous path now fails on intentional regression | [x] |
| W1-R7 | `Evolver` gates 2/3/4 each produce real pass rates from `FixtureValidationReport` | 6.3 | AS-W1.2 | [x] |
| W1-R8 | `assemble_skill_system` default factory produces a working `ValidationExecutor` | 6.3 | Unit test instantiating with no kwargs | [x] |
| W1-R9 | `NotificationService` instantiated in orchestrator process lifespan | 6.4 | Integration test with `FakeDonnaBot`; manual Discord test | [x] |
| W1-R10 | `AutomationScheduler` + `AutomationDispatcher` run in orchestrator, not API | 6.4 | AS-W1.3; smoke that API process has no `automation_scheduler_task` on `app.state` | [x] |
| W1-R11 | API `POST /admin/automations/{id}/run-now` returns 202 and sets `next_run_at=now()` | 6.4 | AS-W1.4 | [x] |
| W1-R12 | Orchestrator scheduler processes `next_run_at=now()` within configured short-poll interval | 6.4 | AS-W1.3 timing assertion | [x] |
| W1-R13 | E2E scenario 1: nightly cycle → drafted skill | 6.5 | `test_wave1_smoke::test_nightly_cycle_drafts_skill` | [x] |
| W1-R14 | E2E scenario 2: automation tick → run + alert | 6.5 | `test_wave1_smoke::test_automation_tick_alerts` | [x] |
| W1-R15 | E2E scenario 3: sandbox → shadow_primary auto-promotion | 6.5 | `test_wave1_smoke::test_sandbox_promotes_to_shadow` | [x] |
| W1-R16 | E2E scenario 4: trusted → flagged_for_review on degradation | 6.5 | `test_wave1_smoke::test_trusted_degrades_to_flagged` | [x] |
| W1-R17 | Existing full test suite passes | 7 acceptance | AS-W1.6 | [x] |
| W1-R18 | Followups doc ticks F-1/F-5/F-6/F-14 | 7 | Doc update | [x] |

---

## 10. Open Questions

1. **Per-tool fingerprint normalization rules** (§6.2). Wave 1 ships rules for `web_fetch`, `gmail_read`, `gmail_send`. Other tools (`calendar_*`, `file_*`) pick up default `(tool_name, json_sorted(args))` fingerprinting until explicit rules are added. Risk: if a default-fingerprinted tool has non-deterministic args, replay breaks. Mitigation: add rules as tools accumulate. Not a Wave 1 blocker.

2. **`automation_run_now_poll_interval_seconds` vs single constant.** §6.4 proposes a short adaptive poll after a run-now request. Simpler alternative: a single 15s constant poll interval. If adaptive polling adds meaningful implementation complexity during the plan, fall back to 15s constant and document.

3. **Test-only `FakeDonnaBot` vs production bot interface extraction.** F-14 needs a fake bot. Two approaches: (a) extract a `BotProtocol` typing.Protocol that both `DonnaBot` and `FakeDonnaBot` implement, or (b) duck-type and document. (a) is better hygiene; (b) is Wave 1-expedient. The plan should choose.

4. **SMS and Gmail integrations on `NotificationService`.** §6.4 sets `sms=None, gmail=None` in Wave 1. If the existing orchestrator process already has `TwilioSMS` and `GmailClient` instantiated elsewhere, pass them through. Otherwise leave as None and address in Wave 2+.

---

## 11. References

- Predecessor spec: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`.
- Follow-ups inventory: `docs/superpowers/followups/2026-04-16-skill-system-followups.md`.
- `src/donna/skills/startup_wiring.py` — current wiring point for skill-system bundle.
- `src/donna/api/__init__.py` lifespan — current (to-be-moved) automation scheduler wiring.
- `src/donna/cli.py` — orchestrator entrypoint receiving the moved wiring.
- `src/donna/notifications/service.py` — `NotificationService` class, currently never instantiated.
- `src/donna/automations/dispatcher.py` — `AutomationDispatcher` with defensive `self._notifier is not None` check.
- `src/donna/skills/auto_drafter.py` — current location of vacuous `pass_rate=1.0` fallback.
- `src/donna/skills/evolution.py` — `Evolver`, same fallback pattern for gates 2/3/4.
- `config/skills.yaml` — configuration surface for validation thresholds and poll intervals.
