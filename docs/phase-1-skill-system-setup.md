# Skill System Setup Notes (Phase 1 + Phase 2 + Phase 3)

> **For Nick, to remember when activating this on the deployment machine.**
> Last updated: 2026-04-16
> Related spec: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`
> Related plans: `docs/superpowers/plans/2026-04-15-skill-system-phase-1-foundation.md`, `docs/superpowers/plans/2026-04-15-skill-system-phase-2-execution.md`, `docs/superpowers/plans/2026-04-15-skill-system-phase-3-lifecycle.md`

Phase 1 and Phase 2 introduced new machinery (capability registry, multi-step skill executor, challenger refactor, tool dispatch, triage, run persistence, dashboard routes) but deliberately ship with the skill system disabled by default — no user-visible behavior change until you actively turn it on. This document lists every action you need to take to activate the skill system on the real deployment, plus the application startup wiring that still needs to be done manually.

---

## 1. Prerequisites

Before activating Phase 1 on the deployment host:

- [ ] **Python 3.12+** in the active venv (unchanged from before).
- [ ] **~200 MB of free disk** in `~/.cache/torch/sentence_transformers/` for the embedding model (`all-MiniLM-L6-v2`, ~80 MB + torch runtime).
- [ ] **Internet access on first run** — the model downloads once, then is cached locally. Subsequent runs are offline-safe.
- [ ] **Dependencies installed**: `sentence-transformers>=3.0.0`, `numpy>=1.26.0`. Run:
  ```bash
  pip install -e .
  ```
  (already declared in `pyproject.toml`; this just ensures the deployment venv has them.)

---

## 2. Database migration

The skill system added seven new tables across both phases:

- **Phase 1**: `capability`, `skill`, `skill_version`, `skill_state_transition`
- **Phase 2**: `skill_run`, `skill_step_result`, `skill_fixture`

Plus two seed migrations that insert the initial capabilities.

### Standard path

If your `donna_tasks.db` has clean Alembic tracking, just:

```bash
alembic upgrade head
```

The four new migrations will apply in order:
1. `a1b2c3d4e5f6` — Phase 1 schema tables; also merges the pre-existing dual Alembic heads (`f1b8c2d4e703` and `f8b2d4e6a913`).
2. `b2c3d4e5f6a7` — seeds `parse_task`, `dedup_check`, `classify_priority`.
3. `c3d4e5f6a7b8` — Phase 2 schema tables (`skill_run`, `skill_step_result`, `skill_fixture`).
4. `d4e5f6a7b8c9` — seeds `fetch_and_summarize` (Phase 2 demo capability).

### If you hit an inconsistent Alembic state

During Phase 1 development (in the worktree), I found a pre-existing inconsistency: the worktree's DB had the `capability` table physically present but Alembic's version table only recorded the two parent revisions. If you see this on the deployment host:

1. Check what's tracked vs. what exists:
   ```bash
   alembic current
   sqlite3 donna_tasks.db ".tables"
   ```
2. If `capability` (or any Phase 1 table) exists but Alembic doesn't think it does, run:
   ```bash
   alembic stamp a1b2c3d4e5f6
   alembic upgrade head
   ```
   This tells Alembic "treat the schema migration as already applied" and then runs only the seed migration on top.

### Verify

```bash
sqlite3 donna_tasks.db "SELECT name, status FROM capability ORDER BY name;"
```

Expected: four rows — `classify_priority|active`, `dedup_check|active`, `fetch_and_summarize|active`, `parse_task|active`.

```bash
sqlite3 donna_tasks.db ".tables" | tr -s ' ' '\n' | grep -E '^skill|^capability'
```

Expected includes: `capability`, `skill`, `skill_version`, `skill_state_transition`, `skill_run`, `skill_step_result`, `skill_fixture`.

---

## 3. Config activation

> **Heads up — `SkillSystemConfig` is currently dead code.** The Pydantic model exists at `src/donna/config.py:SkillSystemConfig` with fields for `enabled`, `match_confidence_high`, `match_confidence_medium`, `similarity_audit_threshold`, and `seed_skills_initial_state`, but **none of its fields are read by runtime code yet.** No top-level config class loads it, and the thresholds that matter are currently hardcoded as module constants:
>
> - `HIGH_CONFIDENCE_THRESHOLD` / `MEDIUM_CONFIDENCE_THRESHOLD` — `src/donna/capabilities/matcher.py:19-20`
> - `SIMILARITY_THRESHOLD` — `src/donna/capabilities/registry.py` (inside `CapabilityRegistry`)
> - `initial_state="sandbox"` — `src/donna/skills/startup.py`
>
> Tuning them means editing those module constants directly until we properly wire `SkillSystemConfig` through a config loader in a later phase.

**To "turn on" the skill system** you don't flip a config value — you pass `skill_routing_enabled=True` to the `AgentDispatcher` constructor (see §4.2). Nothing else in the codebase reads an enabled flag.

---

## 4. Application wiring — TWO THINGS STILL NEED TO BE DONE

These were scoped out of Phase 1 and Phase 2's tasks to keep task-by-task changes tight. They are one-time startup-code edits. Both need to happen before the skill system does anything at runtime.

### 4.1 Wire `initialize_skill_system` into application startup

The startup hook `src/donna/skills/startup.py::initialize_skill_system(conn, skills_dir)` must be called once at application boot. It:
- Generates embeddings for any capability rows with `embedding IS NULL` (uses sentence-transformers).
- Loads any seed skills from `skills/` into the DB for capabilities that don't yet have a skill.
- Builds and returns a `ToolRegistry` populated with built-in tools (currently `web_fetch`; add more by extending `register_default_tools` in `src/donna/skills/tools/__init__.py`).

All operations are idempotent and cheap after the first run. **Capture the returned `ToolRegistry`** — the dispatcher wiring in §4.2 needs it.

**Where to add the call:** find the application startup hook. Look in `src/donna/server.py` (FastAPI app lifespan) or `src/donna/cli.py` (if there's a CLI that boots the service). Expected pattern:

```python
from pathlib import Path
from donna.skills.startup import initialize_skill_system

# Inside the startup/lifespan function, after the DB connection is established
# and BEFORE the dispatcher starts serving traffic:
skill_tool_registry = await initialize_skill_system(db_conn, Path("skills"))
# Store `skill_tool_registry` somewhere reachable from §4.2 (e.g., app.state).
```

Since `SkillSystemConfig` is not yet wired into any top-level config (see §3), there's no flag to check here. Either always call `initialize_skill_system` (it's idempotent and cheap) and decide whether to use the results in §4.2, or guard it with a local boolean constant while we defer config wiring.

### 4.2 Construct the dispatcher + skill execution stack

The Phase 1 dispatcher (`src/donna/orchestrator/dispatcher.py::AgentDispatcher`) gained three optional parameters: `skill_executor`, `skill_database`, `skill_routing_enabled`. Phase 2 added `ToolRegistry`, `TriageAgent`, and `SkillRunRepository` which the executor needs to actually do anything useful. To activate the skill shadow path, the caller that constructs the dispatcher must wire all of this together.

**Where to change:** find the code that instantiates `AgentDispatcher(...)` (likely in `src/donna/server.py` or a startup initializer). Update to:

```python
# Capabilities layer
from donna.capabilities.registry import CapabilityRegistry
from donna.capabilities.matcher import CapabilityMatcher
from donna.capabilities.input_extractor import LocalLLMInputExtractor

# Skills layer
from donna.skills.executor import SkillExecutor
from donna.skills.database import SkillDatabase
from donna.skills.run_persistence import SkillRunRepository
from donna.skills.triage import TriageAgent

# Existing agent
from donna.agents.challenger_agent import ChallengerAgent

# --- Capabilities ---
capability_registry = CapabilityRegistry(db_conn)
capability_matcher = CapabilityMatcher(capability_registry)
input_extractor = LocalLLMInputExtractor(model_router)

# --- Skills infrastructure ---
# skill_tool_registry comes from initialize_skill_system() in §4.1
triage = TriageAgent(model_router)
skill_run_repo = SkillRunRepository(db_conn)
skill_executor = SkillExecutor(
    model_router,
    tool_registry=skill_tool_registry,   # from §4.1
    triage=triage,
    run_repository=skill_run_repo,
)
skill_database = SkillDatabase(db_conn)

# --- Refactored challenger ---
challenger = ChallengerAgent(matcher=capability_matcher, input_extractor=input_extractor)

# --- Dispatcher ---
dispatcher = AgentDispatcher(
    agents={..., "challenger": challenger, ...},
    tool_registry=tool_registry,               # existing agent tool registry, NOT the skill one
    router=model_router,
    db=db,
    project_root=project_root,
    activity_listener=activity_listener,
    skill_executor=skill_executor,             # NEW in Phase 1
    skill_database=skill_database,             # NEW in Phase 1
    skill_routing_enabled=True,                # flip this to activate
)
```

**Important:** the agent `tool_registry` (for PM, prep, scheduler agents — existing) and the skill `tool_registry` (for the skill executor — new, returned from `initialize_skill_system`) are **different objects**. Don't conflate them. The skill executor only knows about tools registered in the skill ToolRegistry; the agents only see tools in the agent ToolRegistry.

---

## 5. Verification

After the migration, config flip, and wiring are done, verify:

### 5.1 Capabilities loaded

```bash
curl http://localhost:8200/admin/capabilities
```

Expected: JSON response with three capabilities (`parse_task`, `dedup_check`, `classify_priority`).

### 5.2 Seed skills loaded

```bash
curl http://localhost:8200/admin/skills
```

Expected: three skills in `sandbox` state (one per capability).

### 5.3 Skill detail view

```bash
curl http://localhost:8200/admin/skills/<skill_id_from_previous_call>
```

Expected: full skill detail including `current_version` with yaml_backbone, step_content, and output_schemas populated.

### 5.4 Send a real message through Donna

Send any normal task to Donna via Discord (e.g., "draft Q2 review by Friday"). Then tail the logs:

```bash
# Look for dispatcher_skill_shadow events
grep -E "dispatcher_skill_shadow|skill_step_completed" /path/to/donna.log
```

You should see:
- `dispatcher_skill_shadow_complete` (or `dispatcher_skill_shadow_no_match` if the challenger didn't match)
- If matched: `skill_step_completed` with latency info

The user-facing response should be **identical** to before — the skill runs in shadow mode in Phase 1, its output is logged but not returned.

---

## 6. Troubleshooting

### Embedding model download hangs or fails

- Symptom: first API call after startup hangs for 30+ seconds, eventually times out.
- Cause: `sentence-transformers` is downloading the model from Hugging Face on first use.
- Fix: ensure internet access, or pre-download: `python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"`.

### Skill shadow path not running

- Symptom: no `dispatcher_skill_shadow_*` log entries despite enabling the flag.
- Likely causes:
  1. The dispatcher wasn't reconstructed with the new params (§4.2).
  2. `skill_routing_enabled=False` — double-check the config plumbing.
  3. The challenger passed to `agents["challenger"]` is the old no-arg constructor — it won't have `match_and_extract`.

### CapabilityMatcher matches the wrong capability

- Expected in early use. Tune `match_confidence_high` and `match_confidence_medium` in `SkillSystemConfig` based on observed match scores. Log entry `capability_match` emits `best_score` for every match attempt — use those to calibrate.

### Seed skills not loading into DB

- Symptom: `initialize_skill_system` runs but `skill` table remains empty.
- Check: does `skills/<capability_name>/skill.yaml` exist for each seed capability? The loader skips skills whose capability isn't in the registry. If a skill's capability_name doesn't match exactly, it's silently skipped (logged as `skill_skipped_no_capability`).

---

## 7. What's NOT active yet

- **Shadow Claude sampling.** Shipped in Phase 3 — see §P3.3. Requires Phase 3 wiring (`ShadowSampler` injected into `SkillExecutor`) and seed skills promoted to `shadow_primary` (Phase 3 migration).
- **Lifecycle state transitions (sandbox → shadow_primary → trusted).** Shipped in Phase 3 — `SkillLifecycleManager` handles automated gates; see §P3.3.
- **Auto-drafting from skill candidates.** Shipped in Phase 3 — `AutoDrafter` + nightly cron; see §P3.4.
- **Novelty judgment Claude call for unmatched task types.** Phase 3 — currently low-confidence matches escalate via the challenger's `escalate_to_claude` status but no skill candidate is registered.
- **Evolution loop for degraded skills.** Phase 4.
- **Automation subsystem.** Phase 5.

---

## Phase 3 — Lifecycle, Shadow Sampling, Auto-Drafting

Phase 3 ships the components that turn seed skills from static sandbox entries into self-improving, auto-promoted skills. This section documents what to run, configure, and wire when deploying Phase 3 code.

---

### P3.1 Migrations

Two new migrations are added on top of the four Phase 1/2 migrations:

- `alembic/versions/add_lifecycle_tables_phase_3.py` — creates `skill_divergence`, `skill_candidate_report`, and `skill_evolution_log`.
- `alembic/versions/promote_seed_skills_to_shadow_primary.py` — promotes the three seed skills (`parse_task`, `dedup_check`, `classify_priority`) from `sandbox` to `shadow_primary` state and writes audit rows into `skill_state_transition`. Only runs if the skills are currently in `sandbox`; idempotent otherwise.

After pulling Phase 3 code:

```bash
alembic upgrade head
```

This adds `skill_divergence`, `skill_candidate_report`, and `skill_evolution_log` and promotes the seed skills. Verify:

```bash
sqlite3 donna_tasks.db "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" | grep -E '^skill'
# Should include: skill, skill_candidate_report, skill_divergence, skill_evolution_log,
#                 skill_fixture, skill_run, skill_state_transition, skill_step_result, skill_version

sqlite3 donna_tasks.db "SELECT s.id, c.name, st.to_state FROM skill s JOIN capability c ON c.id = s.capability_id JOIN skill_state_transition st ON st.skill_id = s.id WHERE st.to_state = 'shadow_primary' ORDER BY c.name;"
# Should show three rows, one per seed skill
```

---

### P3.2 Configuration — `config/skills.yaml`

Phase 3 introduces `config/skills.yaml`. Every knob the skill system reads at runtime lives here. It is loaded once at startup via `load_skill_system_config(config_dir)` and passed into every Phase 3 component.

Create or update `config/skills.yaml` with:

```yaml
# ── Phase 1 knobs ──────────────────────────────────────────────────────────────
enabled: false                           # Master switch
match_confidence_high: 0.75              # CapabilityMatcher HIGH threshold
match_confidence_medium: 0.40            # CapabilityMatcher MEDIUM threshold
similarity_audit_threshold: 0.80         # Flag capability as duplicate if ≥ this
seed_skills_initial_state: sandbox

# ── Phase 3 knobs ──────────────────────────────────────────────────────────────
shadow_sample_rate_trusted: 0.05         # Fraction of trusted runs to shadow-sample
sandbox_promotion_min_runs: 20           # Min runs for sandbox → shadow_primary gate
sandbox_promotion_validity_rate: 0.90    # Fraction of runs that must succeed
shadow_primary_promotion_min_runs: 100   # Min shadow samples for shadow_primary → trusted
shadow_primary_promotion_agreement_rate: 0.85   # Mean agreement threshold
degradation_rolling_window: 30           # Min samples for degradation detection
degradation_ci_confidence: 0.95          # Wilson score CI confidence
auto_draft_daily_cap: 50                 # Max drafts per nightly run
auto_draft_min_expected_savings_usd: 5.0 # Threshold for candidate creation
auto_draft_fixture_pass_rate: 0.80       # Min pass rate for draft acceptance
nightly_run_hour_utc: 3                  # 3 AM UTC scheduled run
```

> **Note:** Until Phase 3 is fully wired, you can leave `enabled: false`. The nightly cron and shadow sampler check this flag. Turning it `true` activates automated promotion and auto-drafting.

---

### P3.3 Wiring the components

At application startup (in `src/donna/server.py` lifespan or equivalent), after the Phase 1/2 wiring from §4, add the Phase 3 component construction:

```python
from donna.config import load_skill_system_config
from donna.skills.candidate_report import SkillCandidateRepository
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.lifecycle import SkillLifecycleManager
from donna.skills.shadow import ShadowSampler
from donna.skills.equivalence import EquivalenceJudge
from donna.skills.detector import SkillCandidateDetector
from donna.skills.auto_drafter import AutoDrafter
from donna.skills.degradation import DegradationDetector

config = load_skill_system_config(config_dir)
lifecycle = SkillLifecycleManager(db.connection, config)
candidate_repo = SkillCandidateRepository(db.connection)
divergence_repo = SkillDivergenceRepository(db.connection)
judge = EquivalenceJudge(model_router)

shadow_sampler = ShadowSampler(
    model_router=model_router, judge=judge,
    divergence_repo=divergence_repo, config=config,
    lifecycle_manager=lifecycle,
)

# Replace the Phase 2 SkillExecutor construction with this (adds shadow_sampler):
executor = SkillExecutor(
    model_router=model_router,
    tool_registry=tool_registry,
    triage=triage,
    run_repository=run_repository,
    shadow_sampler=shadow_sampler,     # NEW in Phase 3
)

detector = SkillCandidateDetector(db.connection, candidate_repo, config)
degradation = DegradationDetector(db.connection, divergence_repo, lifecycle, config)
auto_drafter = AutoDrafter(
    connection=db.connection,
    model_router=model_router,
    budget_guard=budget_guard,
    candidate_repo=candidate_repo,
    lifecycle_manager=lifecycle,
    config=config,
    executor_factory=lambda: executor,   # Or a sandbox-safe variant
)

# Make available to dashboard endpoints:
app.state.skill_lifecycle_manager = lifecycle
app.state.auto_drafter = auto_drafter
```

**Important:** `ShadowSampler` is injected into `SkillExecutor`. After each successful trusted-skill run, the executor fires an `asyncio.create_task` (non-blocking) that calls `ShadowSampler.maybe_sample(...)`. The sampler respects `shadow_sample_rate_trusted` and will not slow down the hot path.

---

### P3.4 Scheduler entry — nightly cron

The nightly job must fire at `config.nightly_run_hour_utc` (default 3 AM UTC). Add to your APScheduler setup or `cron.py`:

```python
from donna.skills.crons.nightly import NightlyDeps, run_nightly_tasks

async def nightly_job():
    deps = NightlyDeps(
        detector=detector, auto_drafter=auto_drafter, degradation=degradation,
        cost_tracker=cost_tracker,
        daily_budget_limit_usd=config_models.cost.daily_pause_threshold_usd,
        config=skill_config,
    )
    report = await run_nightly_tasks(deps)
    logger.info("nightly_skill_tasks_done", report=report)

# Schedule at 3 AM UTC (or config.nightly_run_hour_utc).
```

`run_nightly_tasks` runs three sub-jobs in sequence:
1. `SkillCandidateDetector.detect_candidates()` — scans `skill_run` for high-frequency claude_native patterns and creates `skill_candidate_report` rows.
2. `AutoDrafter.run_batch()` — for each pending candidate above the savings threshold, asks Claude to generate a skill YAML and validates it against fixtures. Stops when `auto_draft_daily_cap` is reached or the day's budget limit is hit.
3. `DegradationDetector.run()` — computes Wilson-score confidence intervals over recent divergence rows and transitions any skill whose CI lower bound falls below the agreement threshold to `flagged_for_review`.

---

### P3.5 New API routes

Phase 3 adds the following admin routes (all under the `/admin` prefix):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/admin/skill-candidates` | List candidates; filter by `status` query param |
| `POST` | `/admin/skill-candidates/{id}/dismiss` | Dismiss a candidate without drafting |
| `POST` | `/admin/skill-candidates/{id}/draft-now` | Trigger immediate auto-draft for a candidate |
| `GET` | `/admin/skill-drafts` | List skills currently in `draft` state |
| `POST` | `/admin/skills/{id}/state` | Manual state transition (body: `{"to_state": "..."}`) |
| `POST` | `/admin/skills/{id}/flags/requires_human_gate` | Toggle the `requires_human_gate` flag |
| `GET` | `/admin/skill-runs/{id}/divergence` | Fetch the `skill_divergence` row for a run |

These routes are registered in `src/donna/api/routes/admin_skills.py` and must be mounted on the FastAPI app in `src/donna/server.py`:

```python
from donna.api.routes.admin_skills import router as admin_skills_router
app.include_router(admin_skills_router)
```

---

### P3.6 Verification

After migrating and wiring Phase 3:

```bash
# New tables exist
sqlite3 donna_tasks.db ".tables" | tr -s ' ' '\n' | grep -E '^skill'
# Expected: includes skill_divergence, skill_candidate_report, skill_evolution_log

# Seed skills are in shadow_primary
sqlite3 donna_tasks.db "SELECT s.id, c.name, s.current_state FROM skill s JOIN capability c ON c.id = s.capability_id;"
# Expected: parse_task|shadow_primary, dedup_check|shadow_primary, classify_priority|shadow_primary

# New admin routes respond
curl http://localhost:8200/admin/skill-candidates
curl http://localhost:8200/admin/skill-drafts
```

Send a task through Donna and check for shadow sampling in logs:

```bash
grep -E "shadow_sample|equivalence_judge|skill_divergence" /path/to/donna.log
```

You should see `shadow_sampler_skipped` (rate-throttled) or `shadow_sample_complete` with an `agreement` field.

---

## 8. Quick activation checklist

- [ ] Dependencies installed (`pip install -e .` — pulls `sentence-transformers`, `numpy`, `httpx`)
- [ ] Database migration run (`alembic upgrade head`) — applies all 4 new migrations
- [ ] Four seed capabilities verified in DB (including `fetch_and_summarize`)
- [ ] All 7 new tables exist (capability, skill, skill_version, skill_state_transition, skill_run, skill_step_result, skill_fixture)
- [ ] `initialize_skill_system(db_conn, Path("skills"))` wired into application startup and its returned `ToolRegistry` captured (§4.1)
- [ ] `AgentDispatcher` constructor updated with `skill_executor`, `skill_database`, `skill_routing_enabled=True` (§4.2)
- [ ] `SkillExecutor` constructed with `tool_registry` (from startup), `triage`, and `run_repository` (§4.2)
- [ ] `ChallengerAgent` constructed with `matcher` and `input_extractor` (§4.2)
- [ ] App restarted
- [ ] `/admin/capabilities` returns four rows
- [ ] `/admin/skills` returns four sandbox skills
- [ ] `/admin/skill-runs` returns an empty list initially
- [ ] Sending a real task produces `dispatcher_skill_shadow_*` and `skill_step_completed` log events
- [ ] A `skill_run` row appears in the DB after each matched message
- [ ] User-facing behavior unchanged
