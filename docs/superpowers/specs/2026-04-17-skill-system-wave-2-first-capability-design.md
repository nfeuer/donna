# Skill System Wave 2 — Hardening + First Real Capability

**Status:** Draft
**Author:** Nick (with brainstorming assistance from Claude)
**Date:** 2026-04-17
**Scope:** Medium-large. Ten deliverables bundled. 4–7 days of focused work including skill YAML + fixtures.
**Predecessor:** `2026-04-16-skill-system-wave-1-production-enablement-design.md` (Wave 1 shipped in PR #44).

---

## 1. Overview

Wave 1 made `skill_system.enabled=true` safe in production. But the skill-system pipeline is still idle — no real capability exists, and a final code review surfaced two P0 correctness bugs (`EvolutionGates` loses `tool_mocks`; `SkillExecutor` passes kwargs `ModelRouter.complete` doesn't accept) that would fire the first time a real tool-using skill ran. Wave 2 closes both, polishes the Wave 1 followups (F-W1-D through F-W1-H), and seeds the first real user-facing capability: `product_watch`.

After Wave 2, Nick can create a `product_watch` automation via `POST /admin/automations`, Donna will monitor a real URL (via `web_fetch`), extract price/availability/size, evaluate the alert condition, and dispatch a Discord DM when the condition fires — all through the skill system running on local Ollama, with Claude shadowing in `sandbox` lifecycle state until 20 schema-valid runs promote it to `shadow_primary`.

Wave 2 is **pipeline hardening + first real usage**. It deliberately does NOT include Discord natural-language creation (F-3), dashboard UI (F-4), or additional capabilities (news_check, meeting_prep). Those are Waves 3+.

---

## 2. Out of Scope

| # | What | Why deferred | When / in which wave |
|---|---|---|---|
| OOS-W2-1 | F-3 Discord natural-language automation creation | Challenger refactor is substantive; needs its own wave. The motivating "watch this URL" UX lands in Wave 3. | **Wave 3.** |
| OOS-W2-2 | F-4 Dashboard UI | Separate project track; needs its own brainstorm cycle with frontend approach decided (SPA vs Flutter-extend vs admin HTML). | Wave 4+. |
| OOS-W2-3 | Additional seed capabilities (news_check, meeting_prep, generate_digest, etc.) | Seed ONE capability end-to-end first; validate the whole pipeline against real behavior before replicating. | Wave 4+ on demand. |
| OOS-W2-4 | Provider-level structured output (Ollama `format: <schema>`, Claude tool-use schemas) | Wave 2 keeps post-hoc `validate_output`. Measure triage retry rate first; only invest in provider-level enforcement if reliability data demands it. | When retry rate > 20% of skill LLM calls. |
| OOS-W2-5 | Price history tracking on `product_watch` | Minimal v1 reports current price only; no new table for history. | When there's a concrete use case that needs a timeseries. |
| OOS-W2-6 | Vendor detection / redirect following / user-agent tuning in `web_fetch` | Base `web_fetch` is sufficient for v1. | When a real fixture fails for this reason. |
| OOS-W2-7 | Migrating existing Claude-native task types (`parse_task`, `dedup_check`, `classify_priority`) to capabilities | The seed skills already exist for these (Wave 1 Phase 1 work). Re-migrating to capabilities is F-13 territory — do after product_watch validates the full lifecycle end-to-end. | Wave 4+. |

---

## 3. Core Concepts and Vocabulary

Wave 2 introduces one new concept and extends two existing ones.

**Mock synthesis.** The process of re-keying a `skill_run.tool_result_cache` blob (cache_id-keyed `{tool, args, result}` entries) into the fingerprint-keyed shape used by `skill_fixture.tool_mocks`. Shared by the Alembic backfill (added in Wave 1) and the runtime EvolutionGates (added in Wave 2). New module: `src/donna/skills/mock_synthesis.py`.

**Manual draft trigger.** A new column `skill_candidate_report.manual_draft_at` that the API can set to request immediate drafting. The orchestrator polls for rows with `manual_draft_at IS NOT NULL AND status='new'` on its automation scheduler cadence. Mirrors the `automation.next_run_at` pattern we shipped in Wave 1.

**Captured-run fixture.** A skill_fixture with `source='captured_from_run'`, `expected_output_shape` inferred via `json_to_schema(skill_run.final_output)`, and `tool_mocks` synthesized via `mock_synthesis.cache_to_mocks(skill_run.tool_result_cache)`. Wave 2 wires this via a new `POST /admin/skill-runs/{id}/capture-fixture` endpoint.

**Validation task_type tagging.** When `ValidationExecutor` runs a skill step, the task_type passed to the model router is `f"skill_validation::{capability_name}::{step_name}"` (was uniformly `skill_step::...`). Production skill calls stay `skill_step::...`. Distinct prefixes → clean filter in `invocation_log` for cost attribution (`WHERE task_type LIKE 'skill_validation::%'`).

---

## 4. Architecture

### 4.1 No process split changes

Wave 1's process split stands unchanged:
- `donna-orchestrator` (port 8100) owns schedulers, dispatch, NotificationService.
- `donna-api` (port 8200) owns REST CRUD.
- DB-mediated coordination via `automation.next_run_at` and (new in Wave 2) `skill_candidate_report.manual_draft_at`.

### 4.2 Flow: `product_watch` automation runs end-to-end

```
User: POST /admin/automations  (capability_name=product_watch,
                                inputs={url, max_price_usd, required_size}...)
   │
   ▼ (automation row created, next_run_at set by cron)
   │
Orchestrator scheduler (15s poll):
   │
   ▼
AutomationDispatcher.dispatch(automation)
   │
   ├── skill.state >= shadow_primary? → SkillExecutor path
   │                                    (won't fire on day 1 — seed skill
   │                                     starts in sandbox lifecycle state)
   │
   └── else → claude_native path
        │
        ▼
   Claude runs product_watch prompt against real web_fetch
   Output: {price_usd, in_stock, size_available, triggers_alert}
        │
        ▼
   Alert condition evaluator: matches → NotificationService.dispatch(Discord DM)
        │
        ▼
   automation_run persisted with claude_native execution_path

In parallel: once the seed skill is loaded (sandbox lifecycle state),
shadow sampling kicks in — the skill runs on local Ollama alongside Claude,
outputs compared, skill_divergence rows written. After 20 schema-valid
shadow runs the skill auto-promotes to shadow_primary (via lifecycle check).
```

### 4.3 Flow: evolution validation with real tool_mocks (F-W1-B fix)

```
Evolver detects degradation → produces candidate new version →
    EvolutionGates.run(new_version, skill)
        │
        ├── Gate 1 structural validation: new_version parses, schemas valid
        ├── Gate 2 targeted-case improvement (10 captured failure runs):
        │     for each run:
        │       mocks = mock_synthesis.cache_to_mocks(run.tool_result_cache)
        │       executor.execute(..., tool_mocks=mocks)
        │     pass_rate >= 0.80?
        ├── Gate 3 fixture-regression (full fixture library):
        │     SELECT input, expected_output_shape, tool_mocks FROM skill_fixture
        │     for each fixture:
        │       executor.execute(..., tool_mocks=json.loads(fixture.tool_mocks or "{}"))
        │     pass_rate >= 0.95?
        ├── Gate 4 recent-success (20 captured success runs):
        │     same as Gate 2, all must pass schema
        │
All pass → version persisted, skill → draft (requires human approval to sandbox)
Any fail → outcome=rejected_validation, count toward 2-strike claude_native demotion
```

### 4.4 Component interactions

| Component | Before Wave 2 | After Wave 2 |
|---|---|---|
| `src/donna/skills/executor.py` `_run_llm_step` | Passes `schema=...`, `model_alias="local_parser"` to `ModelRouter.complete`. Router doesn't accept these. TypeErrors on first real run. | `schema` and `model_alias` kwargs deleted. Relies on executor's post-hoc `validate_output`. Router unchanged. |
| `src/donna/skills/triage.py` | Same mismatch as executor. | Same deletion. |
| `src/donna/skills/evolution_gates.py` | Targeted-case, fixture-regression, recent-success gates call `executor.execute(...)` with no `tool_mocks`. First tool step raises `UnmockedToolError`, gate counts as failure. | Fixture-regression gate SELECTS `skill_fixture.tool_mocks` and threads it. Targeted-case + recent-success gates synthesize mocks from `skill_run.tool_result_cache` via `mock_synthesis.cache_to_mocks`. |
| `src/donna/skills/validation_executor.py` | Wraps executor; passes through task_type unchanged. | Rewrites task_type: each step's task_type becomes `f"skill_validation::{capability}::{step}"` before the inner executor runs it. New constructor param or `execute(..., task_type_prefix=...)`. |
| `src/donna/skills/validation_executor.py` (timeout) | Per-run timeout wraps the whole execute call (60s default). Per-step timeout field exists but isn't consumed. | Per-step timeout wraps `self._router.complete(...)` INSIDE `_run_llm_step` when `run_sink` is set (validation mode only). Production skill runs keep no step timeout. |
| `src/donna/api/routes/skill_candidates.py` `draft-now` | Returns 501 (Wave 1 cleanup). | Sets `skill_candidate_report.manual_draft_at = now()`. Returns 202. |
| `src/donna/cli.py` | Automation scheduler + dispatcher wired only when `skill_config.enabled=True`. | Automation wiring moves OUT of the `if skill_config.enabled` guard. Automation subsystem has its own presence gate (any active automations exist → scheduler runs). |
| `src/donna/cli.py` orchestrator loop | Nightly cron runs `run_nightly_tasks` which includes `run_new_candidates`. | Nightly still runs. Additionally, an hourly/15s-poll path picks up candidates with `manual_draft_at IS NOT NULL`. |
| `src/donna/automations/dispatcher.py` | Does not pass `automation_run_id` into `executor.execute(...)`. Skill runs triggered via automations can't back-reference the automation. | Passes `automation_run_id` into `executor.execute(...)`. `SkillRunRepository.start_run` writes it to `skill_run.automation_run_id`. `SkillRunResult.run_id` added; dispatcher writes it back to `automation_run.skill_run_id`. Both directions linked. |
| `src/donna/skills/correction_cluster.py` / `src/donna/correction_log.py` | `CorrectionClusterDetector.scan_once()` called only from nightly cron. Users wait up to 24h to see a corrected skill flagged. | `CorrectionLogRepository.record_correction(...)` calls `detector.scan_for_skill(skill_id)` synchronously after inserting the correction row. Urgent notification fires within seconds. Nightly scan kept as belt-and-suspenders. |
| `config/capabilities.yaml` | Does not exist. | New file. Declares seed capabilities including `product_watch`. Loaded at orchestrator startup; rows inserted into `capability` table if absent. |
| `skills/product_watch/` | Does not exist. | New directory. Hand-written YAML + per-step markdown + per-step schemas + 4 fixtures with `tool_mocks`. Seeded into DB at startup (similar to existing `seed_fetch_and_summarize` Alembic migration). |
| `src/donna/skills/mock_synthesis.py` | Does not exist. | New module. `cache_to_mocks(tool_result_cache: dict) -> dict[str, Any]` — re-keys cache entries into fingerprint-keyed mocks. Shared between runtime (EvolutionGates, capture-fixture endpoint) and conceptually duplicated in Alembic migration. |

### 4.5 New directories and files

```
src/donna/skills/
  mock_synthesis.py                          # cache-to-mocks re-keyer

skills/product_watch/
  skill.yaml                                 # Skill backbone (YAML)
  steps/
    fetch_page.md                            # (tool step; no LLM content)
    extract_product_info.md                  # LLM prompt
    format_output.md                         # LLM prompt
  schemas/
    extract_product_info_v1.json             # output schema
    format_output_v1.json                    # output schema
  fixtures/
    in_stock_below_threshold.json
    in_stock_above_threshold.json
    sold_out.json
    url_404.json

config/capabilities.yaml                     # Seed capability declarations

alembic/versions/
  add_manual_draft_at.py                     # skill_candidate_report.manual_draft_at
  seed_product_watch_capability.py           # capability + skill + skill_version + fixtures

src/donna/api/routes/
  (no new files; skill_runs.py gains capture-fixture endpoint)

tests/e2e/
  test_wave2_product_watch.py                # end-to-end product_watch run
```

---

## 5. Data Model

### 5.1 `skill_candidate_report.manual_draft_at` column

```
manual_draft_at       TEXT  -- ISO-8601 UTC; NULL unless manually requested
```

**Usage.**
- Set by `POST /admin/skill-candidates/{id}/draft-now` via the API.
- Read by a new orchestrator-side `ManualDraftPoller` (or the existing `AutomationScheduler` extended with a second poll target).
- On pickup: orchestrator runs `AutoDrafter.draft_one(candidate)` then clears `manual_draft_at`.
- Indexed for fast `WHERE manual_draft_at IS NOT NULL AND status = 'new'` scans.

**Migration.** `add_manual_draft_at.py` — adds the column + index. Nullable; no backfill needed.

### 5.2 No other schema changes

`product_watch` seed uses existing tables (`capability`, `skill`, `skill_version`, `skill_fixture`). F-2 linkage uses existing `automation_run.skill_run_id` and `skill_run.automation_run_id` columns that already exist but aren't populated today — no schema change needed, only writer changes.

---

## 6. Subsystem Designs

### 6.1 F-W1-C: router kwargs deletion

**Change.** In `src/donna/skills/executor.py:454-458` and `src/donna/skills/triage.py:80-84`, delete `schema=...` and `model_alias=...` kwargs from the `self._router.complete(...)` call.

**Pre-conditions.** `config/task_types.yaml` must have all `skill_step::*` and `skill_validation::*` and `triage::*` prefixes configured to route to `local_parser`. Before landing, audit `config/task_types.yaml`:

```yaml
# Verify these route_map entries exist. If not, add them with alias: local_parser:
- skill_step::*
- skill_validation::*
- triage_failure
```

The router's current wildcard logic (see `_resolve_route`) handles glob-style `prefix::*` keys. If it doesn't, extend it to — this is a prerequisite for (B) to work and is small.

**Validation.** After the deletion:
1. Unit test `test_run_llm_step_calls_router_without_extra_kwargs` — uses a real `MagicMock(spec=ModelRouter)`, calls `_run_llm_step`, asserts mock was called with exactly the right kwargs (no extras).
2. Integration test `test_executor_against_real_router_fake_provider` — wires a real `ModelRouter` with a fake `OllamaProvider`, runs a single-step skill, asserts no TypeError.
3. E2E smoke (existing `test_wave1_smoke.py`) continues to pass — the fake router in the harness must also be updated to be `spec=ModelRouter` so kwarg mismatches surface.

**Config implications.** `config/task_types.yaml` entries for validation prefixes (see §6.4 below for the `skill_validation::` scheme).

### 6.2 F-W1-B: EvolutionGates thread `tool_mocks`

**Module:** `src/donna/skills/mock_synthesis.py` (new). Contains:

```python
def cache_to_mocks(tool_result_cache: dict) -> dict[str, Any]:
    """Re-key a skill_run.tool_result_cache into fingerprint-keyed mocks.

    Input: {cache_id: {"tool": str, "args": dict, "result": Any}}
    Output: {f"{tool}:{canonical_json(args)}": result}

    Uses the default (sorted-JSON) fingerprint for tools without explicit
    rules — matches donna.skills.tool_fingerprint.fingerprint() output for
    the non-rule case. For tools WITH explicit rules in tool_fingerprint,
    this helper ALSO applies the rule (so fingerprints match at dispatch
    time). The Alembic migration (Wave 1) cannot import this helper, so
    it uses canonical-JSON only — which works for captured runs whose
    tools all lack explicit rules at backfill time.
    """
```

The runtime version imports `donna.skills.tool_fingerprint.fingerprint` so rule-based tools synthesize correctly. The migration keeps its inlined duplicate. Documentation note in both files captures the intentional divergence.

**Gate 3 (fixture-regression) change.** In `src/donna/skills/evolution_gates.py`:

```python
# Before:
cursor = await conn.execute(
    "SELECT id, input, expected_output_shape FROM skill_fixture WHERE skill_id = ?",
    (skill.id,),
)

# After:
cursor = await conn.execute(
    "SELECT id, input, expected_output_shape, tool_mocks FROM skill_fixture WHERE skill_id = ?",
    (skill.id,),
)

# Per fixture:
mocks = json.loads(row[3]) if row[3] else None
result = await self._executor.execute(
    skill=skill, version=new_version,
    inputs=json.loads(row[1]),
    user_id="evolution_harness",
    tool_mocks=mocks,
)
```

**Gate 2 (targeted-case) and Gate 4 (recent-success) change.** These iterate captured `skill_run` rows. Each run has `tool_result_cache`. Thread through `mock_synthesis.cache_to_mocks`:

```python
cursor = await conn.execute(
    "SELECT id, state_object, tool_result_cache FROM skill_run WHERE id IN (?, ?, ...)",
    ...,
)
for row in await cursor.fetchall():
    cache = json.loads(row[2]) if row[2] else {}
    mocks = cache_to_mocks(cache)
    # Reconstruct inputs from state_object (existing code).
    inputs = json.loads(row[1]).get("inputs", {})
    result = await self._executor.execute(
        skill=skill, version=new_version, inputs=inputs,
        user_id="evolution_harness", tool_mocks=mocks,
    )
```

**Test coverage.** New `test_evolution_gates_with_real_tool_mocks.py` — constructs a `ValidationExecutor` + a skill with one `web_fetch` tool step, seeds a `skill_fixture(tool_mocks={...})`, runs Gate 3, asserts pass. Without the fix, this test would fail with `UnmockedToolError`. Prevents regression.

### 6.3 F-W1-E: per-step timeout (validation-only)

**Change.** In `src/donna/skills/executor.py` `_run_llm_step`, wrap the `self._router.complete(...)` call in `asyncio.wait_for(...)` ONLY when `self._run_sink is not None`:

```python
if self._run_sink is not None:
    # Validation mode — enforce per-step timeout.
    timeout = getattr(self._config, "validation_per_step_timeout_s", 60)
    output, meta = await asyncio.wait_for(
        self._router.complete(prompt=..., task_type=...),
        timeout=timeout,
    )
else:
    # Production — no per-step timeout (local Ollama can legitimately take long).
    output, meta = await self._router.complete(prompt=..., task_type=...)
```

**But:** `SkillExecutor` currently doesn't hold a reference to `SkillSystemConfig`. Add one: `SkillExecutor.__init__(..., config: SkillSystemConfig | None = None)`. When `None`, skip the timeout. `ValidationExecutor` passes its config through.

**Fallback.** If `asyncio.TimeoutError` fires, the step raises; the executor's existing exception handler catches it and marks the step failed with reason `per_step_timeout`. Triage decides retry/escalate.

### 6.4 F-W1-G: validation task_type prefix

**Change.** Add `task_type_prefix: str | None = None` to `SkillExecutor.execute(...)` signature (existing `**_ignored_kwargs` absorbs it for non-validation callers). When set, `_run_llm_step` rewrites the step's task_type:

```python
task_type = f"skill_step::{capability_name}::{step.name}"
if self._task_type_prefix:
    task_type = f"{self._task_type_prefix}::{capability_name}::{step.name}"
```

Actually cleaner: `SkillExecutor` stores `self._task_type_prefix` from kwargs during `execute`, cleared after the run. Alternative: constructor param, passed at init.

**Recommend:** constructor param on `SkillExecutor`. `ValidationExecutor` passes `task_type_prefix="skill_validation"` when building its inner executor. Production `SkillExecutor` default is `None` → falls through to `skill_step::` as today.

**Config.** `config/task_types.yaml` must route both prefixes (`skill_step::*` and `skill_validation::*`) to `local_parser`. Add the second prefix to the routing rules as part of this task.

**Verification.** Unit test `test_validation_tags_task_type` — runs `ValidationExecutor` against a 1-step skill with a mock router, asserts the router received `task_type="skill_validation::<cap>::<step>"` for the step call.

### 6.5 F-W1-H: automation subsystem independence

**Change.** In `src/donna/cli.py`, move the automation wiring block OUT of the `if skill_config.enabled:` guard.

Current structure:
```python
if skill_config.enabled:
    # ... skill-system bundle wiring ...
    # ... automation subsystem wiring ...  ← moves out
```

New structure:
```python
if skill_config.enabled:
    # ... skill-system bundle wiring ...

# Automation subsystem — independent of skill_config.enabled. Starts
# whenever the process has a notification_service and a DB connection.
try:
    from donna.automations.alert import AlertEvaluator
    # ... (existing wiring) ...
    automation_scheduler = AutomationScheduler(...)
    tasks.append(asyncio.create_task(automation_scheduler.run_forever()))
    log.info("automation_scheduler_started", ...)
except Exception:
    log.exception("automation_scheduler_wiring_failed")
```

**Skill executor factory:** the dispatcher's `skill_executor_factory` needs to be `None`-safe OR needs access to the skill bundle. Since skill-path automations are OOS-W1-2 and haven't been wired, `lambda: None` remains fine. When the skill system is enabled, the factory COULD resolve a real executor from the bundle — but that's a later enhancement, not Wave 2 scope.

**Verification.** Integration test `test_automation_runs_with_skill_system_disabled` — sets `skill_config.enabled=false`, wires the orchestrator, creates an automation, triggers run-now, asserts the `automation_run` row is created and claude_native execution happens.

### 6.6 F-W1-D: manual draft trigger

**Migration.** `alembic/versions/add_manual_draft_at.py`:
```python
def upgrade() -> None:
    with op.batch_alter_table("skill_candidate_report") as batch_op:
        batch_op.add_column(sa.Column("manual_draft_at", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_skill_candidate_report_manual_draft_at",
            ["manual_draft_at"],
        )
```

**API endpoint change.** In `src/donna/api/routes/skill_candidates.py`, replace the 501 body with:
```python
@router.post("/skill-candidates/{candidate_id}/draft-now", status_code=202)
async def draft_candidate_now(candidate_id: str, db: Database = Depends(get_db)) -> dict:
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    cursor = await db.connection.execute(
        "UPDATE skill_candidate_report SET manual_draft_at = ? "
        "WHERE id = ? AND status = 'new'",
        (now_iso, candidate_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, detail="candidate not found or not in 'new' status")
    await db.connection.commit()
    return {"status": "scheduled", "manual_draft_at": now_iso}
```

**Orchestrator pickup.** Add a new `ManualDraftPoller` under `src/donna/skills/manual_draft_poller.py`:
```python
class ManualDraftPoller:
    def __init__(self, connection, auto_drafter, candidate_repo, poll_interval_s: int = 15):
        ...

    async def run_once(self) -> int:
        cursor = await self._conn.execute(
            "SELECT id FROM skill_candidate_report "
            "WHERE manual_draft_at IS NOT NULL AND status = 'new' "
            "ORDER BY manual_draft_at ASC LIMIT 5"
        )
        picked = 0
        for (candidate_id,) in await cursor.fetchall():
            candidate = await self._repo.get(candidate_id)
            if candidate is None:
                continue
            await self._auto_drafter.draft_one(candidate)
            await self._conn.execute(
                "UPDATE skill_candidate_report SET manual_draft_at = NULL WHERE id = ?",
                (candidate_id,),
            )
            await self._conn.commit()
            picked += 1
        return picked

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("manual_draft_poller_tick_failed")
            await asyncio.sleep(self._poll_interval_s)
```

Wired in `cli.py` alongside the automation scheduler. Uses the same 15s cadence.

**Verification.** Integration test: set `manual_draft_at`, run `poller.run_once()`, assert `auto_drafter.draft_one` was called and the column is cleared.

### 6.7 F-W1-F: capture-fixture endpoint

**New endpoint.** `POST /admin/skill-runs/{run_id}/capture-fixture` in `src/donna/api/routes/skill_runs.py`:
```python
@router.post("/skill-runs/{run_id}/capture-fixture", status_code=201)
async def capture_fixture(run_id: str, case_name: str | None = None,
                           db: Database = Depends(get_db)) -> dict:
    # Load the run.
    cursor = await db.connection.execute(
        "SELECT id, skill_id, final_output, tool_result_cache, status "
        "FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(404, detail="skill_run not found")
    if row[4] != "succeeded":
        raise HTTPException(409, detail="can only capture from succeeded runs")

    from donna.skills.schema_inference import json_to_schema
    from donna.skills.mock_synthesis import cache_to_mocks

    final_output = json.loads(row[2]) if row[2] else {}
    cache = json.loads(row[3]) if row[3] else {}

    expected_shape = json_to_schema(final_output)
    tool_mocks = cache_to_mocks(cache)

    # Inputs are reconstructed from state_object or the linked task_id — for v1
    # take from state_object:
    cursor = await db.connection.execute(
        "SELECT state_object FROM skill_run WHERE id = ?", (run_id,),
    )
    state_row = await cursor.fetchone()
    inputs = json.loads(state_row[0]).get("inputs", {}) if state_row[0] else {}

    # Create the skill_fixture row via the existing _persist_fixture helper.
    from donna.skills.auto_drafter import _persist_fixture
    fixture_id = await _persist_fixture(
        conn=db.connection,
        skill_id=row[1],
        case_name=case_name or f"captured_from_{run_id[:8]}",
        input_=inputs,
        expected_output_shape=expected_shape,
        tool_mocks=tool_mocks if tool_mocks else None,
        source="captured_from_run",
        captured_run_id=run_id,
    )
    await db.connection.commit()
    return {"fixture_id": fixture_id, "source": "captured_from_run"}
```

**Test.** Unit test — seed a `skill_run` with `final_output + tool_result_cache`, POST the endpoint, assert the `skill_fixture` row's `expected_output_shape` matches `json_to_schema(final_output)` and `tool_mocks` matches `cache_to_mocks(tool_result_cache)`.

### 6.8 F-2: automation_run.skill_run_id linkage

**`SkillRunResult` extension.** Add `run_id: str | None = None` to `src/donna/skills/executor.py:SkillRunResult`. The executor sets it from `SkillRunRepository.start_run(...)`' return value.

**Dispatcher change.** In `src/donna/automations/dispatcher.py`:
```python
# Before:
result = await executor.execute(skill=..., version=..., inputs=..., user_id=...)

# After:
result = await executor.execute(
    skill=..., version=..., inputs=..., user_id=...,
    automation_run_id=automation_run_id,  # threaded through to SkillRunRepository
)

# After the run:
if result.run_id is not None:
    skill_run_id = result.run_id

# During finish_run:
await self._repo.finish_run(
    run_id=run_id, ..., skill_run_id=skill_run_id, ...,
)
```

**Executor change.** `SkillExecutor.execute` threads `automation_run_id` into `SkillRunRepository.start_run(..., automation_run_id=automation_run_id)`. This writes `skill_run.automation_run_id` → reverse direction.

**Verification.** E2E: create an automation that triggers a skill-path run (once product_watch is in shadow_primary later — for Wave 2, use the claude_native path and verify `automation_run.invocation_log_id` is populated; for the skill path once it's live). Unit test for the dispatcher writer.

### 6.9 F-7: correction cluster write-hook

**Change.** In the correction-log write path (search: `grep -n "record_correction\|correction_log" src/donna/`), add a synchronous call to `CorrectionClusterDetector.scan_for_skill(skill_id)`:

```python
async def record_correction(
    self,
    task_id: str,
    user_id: str,
    correction_type: str,
    ...
) -> str:
    correction_id = str(uuid6.uuid7())
    # ... existing INSERT into correction_log ...
    await self._conn.commit()

    # Wave 2: fire immediate cluster scan for the affected skill, if resolvable.
    skill_id = await self._resolve_skill_from_task(task_id)
    if skill_id is not None and self._cluster_detector is not None:
        try:
            await self._cluster_detector.scan_for_skill(skill_id)
        except Exception:
            logger.exception("correction_cluster_scan_failed",
                             correction_id=correction_id, skill_id=skill_id)

    return correction_id
```

**New method.** `CorrectionClusterDetector.scan_for_skill(skill_id)` — scans just the affected skill's recent correction_log rows (bounded window = `config.correction_cluster_window_runs`). Fires notification if threshold exceeded. Extracted from the existing `scan_once` method which iterates all skills.

**Nightly cron.** `scan_once` stays — belt-and-suspenders.

**Verification.** Integration test: insert 2 corrections for a skill via `record_correction`; assert `NotificationService.dispatch` is called with `NOTIF_SKILL_DEGRADED` (or the existing equivalent).

### 6.10 F-11: seed `product_watch` capability + skill

**Capability declaration.** `config/capabilities.yaml`:
```yaml
capabilities:
  - name: product_watch
    description: |
      Monitor a product URL for price, availability, and size. Returns
      normalized USD price + in-stock flag + availability of the requested size.
    trigger_type: on_schedule
    input_schema:
      type: object
      required: [url]
      properties:
        url:
          type: string
          description: Canonical product URL.
        max_price_usd:
          type: [number, "null"]
          description: Alert when in_stock AND price_usd <= this. Null = any price.
        required_size:
          type: [string, "null"]
          description: e.g. "L", "42 EU". Null = any size available counts.
    default_output_shape:
      type: object
      required: [ok, in_stock]
      properties:
        ok: {type: boolean}
        price_usd: {type: [number, "null"]}
        currency: {type: string}
        in_stock: {type: boolean}
        size_available: {type: boolean}
        triggers_alert: {type: boolean}
        title: {type: string}
```

At orchestrator startup, a new `SeedCapabilityLoader` reads this file and UPSERTs rows into the `capability` table.

**Skill YAML (`skills/product_watch/skill.yaml`):**
```yaml
capability_name: product_watch
version: 1
description: |
  Monitor a product URL for price, availability, and the user's required size.

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

**Step prompt `steps/extract_product_info.md`:**
```
You are extracting product information from HTML. The HTML is in `state.fetch_page.body`.

Return JSON matching this schema:
- price_usd: number (convert non-USD prices; return null if not found)
- currency: string (e.g. "USD", "GBP")
- in_stock: boolean (true if any size is available)
- available_sizes: array of strings (e.g. ["S", "M", "L"])
- title: string (product name)

If the page is a 404 or the product is unavailable, return:
- in_stock: false
- price_usd: null
- available_sizes: []
- title: best-effort guess or "Unknown product"

State object:
{{ state | tojson }}
```

**Step prompt `steps/format_output.md`:**
```
Compute the final output fields from `state.extract_product_info`:

- ok: true (always, unless fetch failed)
- price_usd: state.extract_product_info.price_usd
- currency: state.extract_product_info.currency
- in_stock: state.extract_product_info.in_stock
- size_available: true if (inputs.required_size is null) OR
                  (inputs.required_size IN state.extract_product_info.available_sizes)
- triggers_alert: true if in_stock AND size_available AND
                  (inputs.max_price_usd is null OR price_usd <= inputs.max_price_usd)
- title: state.extract_product_info.title

Inputs: {{ inputs | tojson }}
Extracted info: {{ state.extract_product_info | tojson }}

Return JSON only.
```

**Output schemas** — straightforward JSON Schemas for each step's output. Full examples in the plan.

**Fixtures** (in `skills/product_watch/fixtures/`):

```json
// in_stock_below_threshold.json
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
      "in_stock": {"type": "boolean"},
      "size_available": {"type": "boolean"},
      "triggers_alert": {"type": "boolean"},
      "price_usd": {"type": ["number", "null"]}
    }
  },
  "tool_mocks": {
    "web_fetch:{\"url\":\"https://example-shop.com/shirt-blue\"}": {
      "status": 200,
      "body": "<html>...price $79.00, available sizes M, L, XL...</html>",
      "headers": {}
    }
  }
}
```

Similar fixtures for `in_stock_above_threshold` (price $129), `sold_out` (HTML says out of stock), `url_404` (status 404 → fetch fails → step's `on_failure: fail_step` + skill returns status=escalated).

**Seed migration.** `alembic/versions/seed_product_watch_capability.py` — modeled on existing `seed_skill_system_phase_1.py`. Reads the YAML/md/schemas/fixtures from `skills/product_watch/` and inserts `capability`, `skill` (state=sandbox), `skill_version`, and `skill_fixture` rows.

**Loader.** `SeedCapabilityLoader` at orchestrator startup UPSERTs the capability row (so editing `capabilities.yaml` and restarting is the update path). Skill + version + fixtures go through the Alembic seed migration (one-shot).

**E2E test.** `tests/e2e/test_wave2_product_watch.py`:

```python
@pytest.mark.asyncio
async def test_product_watch_full_pipeline(runtime):
    # 1. Seed capability + skill (normally done by migration; explicit here).
    await _seed_product_watch(runtime.db.connection)

    # 2. Create an automation targeting product_watch.
    automation_id = str(uuid.uuid4())
    await _create_product_watch_automation(
        runtime.db.connection, automation_id,
        url="https://example-shop.com/shirt-blue",
        max_price_usd=100.0,
        required_size="L",
    )

    # 3. Canned Ollama responses for the two LLM steps.
    runtime.fake_ollama.canned["skill_step::product_watch::extract_product_info"] = {
        "price_usd": 79.0, "currency": "USD", "in_stock": True,
        "available_sizes": ["M", "L"], "title": "Blue Shirt"
    }
    runtime.fake_ollama.canned["skill_step::product_watch::format_output"] = {
        "ok": True, "price_usd": 79.0, "currency": "USD",
        "in_stock": True, "size_available": True,
        "triggers_alert": True, "title": "Blue Shirt"
    }

    # 4. Canned web_fetch (via claude_native path since skill state is sandbox).
    # For skill-path runs once shadow_primary, the mock registry handles it.

    # 5. Run the scheduler.
    await runtime.automation_scheduler.run_once()

    # 6. Assertions.
    cursor = await runtime.db.connection.execute(
        "SELECT status, alert_sent FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "succeeded"
    assert row[1] == 1  # alert fired

    # 7. Assert the Discord DM.
    assert len(runtime.fake_bot.sends) >= 1
```

Wave 2 seed skill lands in `sandbox` lifecycle state. The automation's first runs go through the claude_native path (spec §6.9). Shadow sampling accumulates `skill_divergence` rows. After 20 schema-valid shadow runs, auto-promotes to `shadow_primary`. No Wave 2 automation is expected to reach trusted.

---

## 7. Phased Rollout

### Implementation order (must be sequential unless noted)

1. **F-W1-C** router kwargs deletion. Unblocks all downstream real-LLM paths.
2. **F-W1-G** validation task_type prefix (touches executor + config; co-located with F-W1-C).
3. **mock_synthesis.py** helper (new module, prerequisite for F-W1-B and F-W1-F).
4. **F-W1-B** EvolutionGates thread tool_mocks. Uses mock_synthesis.
5. **F-W1-E** per-step timeout (validation-only). Small executor change.
6. **F-W1-H** automation subsystem independence. Small cli.py refactor. Parallel with 7, 8.
7. **F-W1-D** manual_draft_at column + ManualDraftPoller + API 202. Parallel with 6, 8.
8. **F-2** automation_run.skill_run_id linkage. Parallel with 6, 7.
9. **F-7** correction-cluster write-hook. Independent.
10. **F-W1-F** capture-fixture endpoint. Uses mock_synthesis + json_to_schema.
11. **F-11** product_watch capability + skill + seed migration. Depends on all prior items (especially F-W1-B and F-W1-C).
12. **E2E** `test_wave2_product_watch.py`. Depends on F-11.
13. **Docs** — update architecture, followups, spec checklist.

### Handoff contract (Wave 2 → Wave 3)

After Wave 2 merges:
- `ModelRouter.complete` is called with only its declared signature — no stray kwargs. Unit tests use `MagicMock(spec=ModelRouter)` to prevent regression.
- `EvolutionGates` all three gates pass `tool_mocks` through to the executor (synthesized from `tool_result_cache` for captured-run gates; loaded from `skill_fixture.tool_mocks` for fixture-regression).
- `ValidationExecutor` tags LLM calls with `skill_validation::<cap>::<step>`. `config/task_types.yaml` routes the prefix.
- `SkillExecutor` accepts `task_type_prefix` (constructor) and `config` (for per-step timeout); production callers pass `None`.
- Automation subsystem runs whether or not the skill system is enabled.
- `POST /admin/skill-candidates/{id}/draft-now` returns 202 and sets `manual_draft_at`. `ManualDraftPoller` runs in orchestrator.
- `POST /admin/skill-runs/{id}/capture-fixture` exists and returns 201.
- `automation_run.skill_run_id` and `skill_run.automation_run_id` both populated on skill-path automation runs.
- `CorrectionClusterDetector.scan_for_skill(skill_id)` fires synchronously from `record_correction`.
- `product_watch` capability + skill exists; a `POST /admin/automations` with `capability_name='product_watch'` succeeds; a scheduler tick produces a successful `automation_run` with proper alert dispatch.

### Acceptance scenarios

- **AS-W2.1 (manual).** Start orchestrator with `product_watch` seeded, create an automation via `POST /admin/automations` against a real product URL, POST `/admin/automations/{id}/run-now`, wait ≤15s, verify a Discord DM arrives with the rendered alert.
- **AS-W2.2.** Delete the `schema=` kwarg line, rerun the full test suite, expect green (verifies the MagicMock(spec=...) change catches regressions).
- **AS-W2.3.** Construct an `EvolutionGates` with a 1-step web_fetch-using skill, a captured failure run with `tool_result_cache` populated, verify Gate 2 passes (previously would UnmockedTool-fail).
- **AS-W2.4.** `POST /admin/skill-runs/{id}/capture-fixture` creates a `skill_fixture` row with correct `expected_output_shape` and `tool_mocks`.
- **AS-W2.5.** `POST /admin/skill-candidates/{id}/draft-now` returns 202, row has `manual_draft_at`; 15s later, row is cleared and `skill_version` exists.
- **AS-W2.6.** Issue 2 corrections on a trusted skill via the correction-log write path; assert within 1 second, `skill.state = flagged_for_review`.
- **AS-W2.7.** Full E2E `test_wave2_product_watch.py` passes in <10s.
- **AS-W2.8.** Full existing suite (Wave 1 tests + new) green.

---

## 8. Drift Log

*(Empty at authoring.)*

---

## 9. Requirements Checklist

| # | Requirement | Section | Verified by | ✓ |
|---|---|---|---|---|
| W2-R1 | `ModelRouter.complete` called with only its declared kwargs from executor+triage | 6.1 | AS-W2.2 + unit test using `MagicMock(spec=ModelRouter)` | [x] |
| W2-R2 | `EvolutionGates` Gate 3 passes `tool_mocks` from `skill_fixture.tool_mocks` | 6.2 | AS-W2.3 + unit test | [x] |
| W2-R3 | `EvolutionGates` Gate 2 + 4 synthesize mocks via `mock_synthesis.cache_to_mocks` | 6.2 | unit test | [x] |
| W2-R4 | `mock_synthesis.py` shared between runtime and migration (with documented divergence) | 6.2 | inspection + unit test | [x] |
| W2-R5 | Per-step timeout fires in validation runs when step exceeds `validation_per_step_timeout_s` | 6.3 | unit test using monkeypatched slow router | [x] |
| W2-R6 | Per-step timeout does NOT fire in production runs | 6.3 | unit test with `run_sink=None` | [x] |
| W2-R7 | `ValidationExecutor` tags LLM calls `skill_validation::<cap>::<step>` | 6.4 | unit test | [x] |
| W2-R8 | `config/task_types.yaml` routes both `skill_step::*` and `skill_validation::*` to local_parser | 6.4 | inspection + unit test of router resolution | [x] |
| W2-R9 | Automation subsystem runs with `skill_config.enabled=false` | 6.5 | integration test `test_automation_runs_with_skill_system_disabled` | [x] |
| W2-R10 | `skill_candidate_report.manual_draft_at` column exists with index | 6.6 | migration test | [x] |
| W2-R11 | `POST /admin/skill-candidates/{id}/draft-now` returns 202 and sets `manual_draft_at` | 6.6 | integration test | [x] |
| W2-R12 | `ManualDraftPoller.run_once` picks up and clears `manual_draft_at` rows | 6.6 | unit test | [x] |
| W2-R13 | `POST /admin/skill-runs/{id}/capture-fixture` creates a `captured_from_run` fixture | 6.7 | integration test | [x] |
| W2-R14 | `automation_run.skill_run_id` populated on skill-path dispatch | 6.8 | unit + integration test | [x] |
| W2-R15 | `skill_run.automation_run_id` populated when triggered via automation | 6.8 | unit + integration test | [x] |
| W2-R16 | `CorrectionClusterDetector.scan_for_skill` fires synchronously from `record_correction` | 6.9 | integration test AS-W2.6 | [x] |
| W2-R17 | `product_watch` capability row exists with input_schema | 6.10 | migration test | [x] |
| W2-R18 | `product_watch` skill exists in `sandbox` lifecycle state after seed | 6.10 | migration test | [x] |
| W2-R19 | 4 seed fixtures with `tool_mocks` exist for `product_watch` | 6.10 | migration test | [x] |
| W2-R20 | E2E `test_wave2_product_watch.py` passes in <10s | 6.10 | AS-W2.7 | [x] |
| W2-R21 | Full existing suite green | 7 | AS-W2.8 | [x] |
| W2-R22 | Spec checklist + followups doc updated | 7 | Doc update | [x] |

---

## 10. Open Questions

1. **`config/task_types.yaml` wildcard routing.** §6.1 assumes the router's `_resolve_route` supports glob-style `prefix::*` routing keys. If it doesn't today, that becomes a prerequisite task (land first). Investigation during implementation.

2. **Seed migration vs. startup loader for skills.** §6.10 proposes Alembic-seeded skill rows + a startup-time capability loader. Alternative: both via startup loader (idempotent UPSERT). Alembic is simpler for skills (immutable versions) but means editing the YAML and restarting doesn't update the DB — only a new Alembic revision does. Decide during implementation.

3. **`ManualDraftPoller` cadence.** §6.6 proposes 15s to match automation poll. Alternative: share the automation poll loop (single asyncio task that polls both targets). Decide during implementation.

4. **Fixture YAML vs JSON for `product_watch`.** §6.10 shows JSON fixtures. The existing `seed_skill_system_phase_1` migration loads fixtures from JSON. Stay consistent. (Not really open — just confirming.)

5. **`web_fetch` tool implementation in the real runtime.** Wave 2 assumes `web_fetch` is a registered tool in the production ToolRegistry. Verify during F-11 — if the tool doesn't exist yet, F-11 includes registering it. Grep `src/donna/skills/tools/` or similar.

---

## 11. References

- Predecessor spec: `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md` (PR #44).
- Followups inventory: `docs/superpowers/followups/2026-04-16-skill-system-followups.md` (F-W1-A through F-W1-H captured at Wave 1 ship).
- `src/donna/skills/executor.py:454-458` — F-W1-C call site.
- `src/donna/skills/triage.py:78-84` — F-W1-C call site.
- `src/donna/models/router.py:137` — target signature.
- `src/donna/skills/evolution_gates.py` — F-W1-B target.
- `src/donna/skills/validation_executor.py` — F-W1-E + F-W1-G target.
- `src/donna/skills/correction_cluster.py` — F-7 target.
- `alembic/versions/add_fixture_tool_mocks.py` — Wave 1 migration with inlined `_cache_to_mocks` (to be shared with runtime via `mock_synthesis.py`).
- `alembic/versions/seed_skill_system_phase_1.py` — template for the `seed_product_watch_capability` migration.
- `src/donna/api/routes/skill_candidates.py` — F-W1-D 501 → 202 change.
- `config/task_types.yaml` — routing prerequisites for F-W1-C + F-W1-G.
