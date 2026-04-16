# Phase 1 Skill System — Setup Notes

> **For Nick, to remember when activating this on the deployment machine.**
> Last updated: 2026-04-15
> Related spec: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`
> Related plan: `docs/superpowers/plans/2026-04-15-skill-system-phase-1-foundation.md`

Phase 1 introduced new machinery (capability registry, skill executor, challenger refactor, dashboard routes) but deliberately ships with `skill_system.enabled = false` — no user-visible behavior change until you actively turn it on. This document lists every action you need to take to activate Phase 1 on the real deployment, plus the two bits of wiring that still need to be done in application startup code.

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

Phase 1 added four tables: `capability`, `skill`, `skill_version`, `skill_state_transition`. A seed migration also inserts three capabilities: `parse_task`, `dedup_check`, `classify_priority`.

### Standard path

If your `donna_tasks.db` has clean Alembic tracking, just:

```bash
alembic upgrade head
```

The two new migrations will apply in order:
1. `a1b2c3d4e5f6` — creates the four tables and merges the pre-existing dual Alembic heads (`f1b8c2d4e703` and `f8b2d4e6a913`).
2. `b2c3d4e5f6a7` — seeds the three capabilities.

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

Expected: three rows — `classify_priority|active`, `dedup_check|active`, `parse_task|active`.

---

## 3. Config activation

The skill system is gated by `config.skill_system.enabled` (defined in `src/donna/config.py` as `SkillSystemConfig`). Default is `False`.

Phase 1 didn't wire this into any YAML config file — it's currently a Pydantic default. To turn it on, you have two options:

### Option A: inline edit (simplest for a single-user deployment)

Edit `src/donna/config.py` and change `SkillSystemConfig.enabled` default to `True`:

```python
class SkillSystemConfig(BaseModel):
    enabled: bool = True  # flipped from False
    ...
```

### Option B: add to a YAML config file

If you want to set it via `config/donna_models.yaml` or similar, add a block:

```yaml
skill_system:
  enabled: true
  match_confidence_high: 0.75
  match_confidence_medium: 0.40
  similarity_audit_threshold: 0.80
  seed_skills_initial_state: sandbox
```

Then extend whatever config loader reads those files to include `skill_system`. (Not done in Phase 1 because no aggregating top-level config class exists in `src/donna/config.py`.)

Recommended: Option A for now, revisit when we unify config loading in a later phase.

---

## 4. Application wiring — TWO THINGS STILL NEED TO BE DONE

These were scoped out of Phase 1's tasks to keep task-by-task changes tight. They are one-time startup-code edits. Both need to happen before the skill system does anything at runtime.

### 4.1 Wire `initialize_skill_system` into application startup

The startup hook `src/donna/skills/startup.py::initialize_skill_system(conn, skills_dir)` must be called once at application boot. It:
- Generates embeddings for any capability rows with `embedding IS NULL` (uses sentence-transformers).
- Loads any seed skills from `skills/` into the DB for capabilities that don't yet have a skill.

Both operations are idempotent and cheap after the first run.

**Where to add the call:** find the application startup hook. Look in `src/donna/server.py` (FastAPI app lifespan) or `src/donna/cli.py` (if there's a CLI that boots the service). Expected pattern:

```python
from pathlib import Path
from donna.skills.startup import initialize_skill_system
from donna.config import SkillSystemConfig  # or wherever config is loaded

# Inside the startup/lifespan function, after the DB connection is established
# and BEFORE the dispatcher starts serving traffic:
if config.skill_system.enabled:
    await initialize_skill_system(db_conn, Path("skills"))
```

If `config.skill_system` doesn't exist yet (because the top-level config class doesn't have it), either add it to the top-level config or read `SkillSystemConfig` directly from a YAML file.

### 4.2 Construct the dispatcher with the new skill parameters

The Phase 1 dispatcher (`src/donna/orchestrator/dispatcher.py::AgentDispatcher`) gained three optional parameters: `skill_executor`, `skill_database`, `skill_routing_enabled`. They default to `None`/`False`, so existing callers still work unchanged. To activate the skill shadow path, the caller that constructs the dispatcher must pass them.

**Where to change:** find the code that instantiates `AgentDispatcher(...)` (likely in `src/donna/server.py` or a startup initializer). Update to:

```python
from donna.skills.executor import SkillExecutor
from donna.skills.database import SkillDatabase
from donna.agents.challenger_agent import ChallengerAgent
from donna.capabilities.registry import CapabilityRegistry
from donna.capabilities.matcher import CapabilityMatcher
from donna.capabilities.input_extractor import LocalLLMInputExtractor

# Build the skill system components
capability_registry = CapabilityRegistry(db_conn)
capability_matcher = CapabilityMatcher(capability_registry)
input_extractor = LocalLLMInputExtractor(model_router)
skill_executor = SkillExecutor(model_router)
skill_database = SkillDatabase(db_conn)

# Make the challenger skill-aware (replaces the old no-arg construction)
challenger = ChallengerAgent(matcher=capability_matcher, input_extractor=input_extractor)

# Pass everything to the dispatcher
dispatcher = AgentDispatcher(
    agents={..., "challenger": challenger, ...},
    tool_registry=tool_registry,
    router=model_router,
    db=db,
    project_root=project_root,
    activity_listener=activity_listener,
    skill_executor=skill_executor,        # NEW
    skill_database=skill_database,         # NEW
    skill_routing_enabled=config.skill_system.enabled,  # NEW
)
```

If `config.skill_system.enabled` isn't available at that point, pass `True` directly (once you've verified the rest works) or thread the config through.

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

- [ ] Dependencies installed (`pip install -e .`)
- [ ] Database migration run (`alembic upgrade head`)
- [ ] Three seed capabilities verified in DB
- [ ] `SkillSystemConfig.enabled` flipped to `True`
- [ ] `initialize_skill_system` wired into application startup (§4.1)
- [ ] `AgentDispatcher` constructor updated to pass skill components (§4.2)
- [ ] ChallengerAgent constructed with `matcher` and `input_extractor` (§4.2)
- [ ] App restarted
- [ ] `/admin/capabilities` returns three rows
- [ ] `/admin/skills` returns three sandbox skills
- [ ] Sending a real task produces `dispatcher_skill_shadow_*` log events
- [ ] User-facing behavior unchanged
