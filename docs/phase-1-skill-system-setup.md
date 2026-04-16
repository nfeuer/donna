# Skill System Setup Notes (Phase 1 + Phase 2)

> **For Nick, to remember when activating this on the deployment machine.**
> Last updated: 2026-04-16
> Related spec: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`
> Related plans: `docs/superpowers/plans/2026-04-15-skill-system-phase-1-foundation.md`, `docs/superpowers/plans/2026-04-15-skill-system-phase-2-execution.md`

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

- **Shadow Claude sampling.** Phase 3 dependency. Seed skills are in `sandbox`, not `shadow_primary`. The skill's output is logged via `skill_run` events but not compared against Claude yet.
- **Lifecycle state transitions (sandbox → shadow_primary → trusted).** Phase 3.
- **Auto-drafting from skill candidates.** Phase 3.
- **Evolution loop for degraded skills.** Phase 4.
- **Automation subsystem.** Phase 5.
- **Novelty judgment Claude call for unmatched task types.** Phase 3 — currently low-confidence matches escalate via the challenger's `escalate_to_claude` status but no skill candidate is registered.

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
