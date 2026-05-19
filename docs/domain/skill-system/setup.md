# Setup & Activation

How to activate the skill system on a fresh or existing Donna deployment. Covers Phase 1 and Phase 2 foundations.

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

> `SkillSystemConfig` fields are loaded at startup via `cli_wiring.py`. Hardcoded thresholds in matcher/registry serve as fallbacks. *Full wiring tracked as [G-2](../../superpowers/followups/open-backlog.md).*

**To "turn on" the skill system** you don't flip a config value — you pass `skill_routing_enabled=True` to the `AgentDispatcher` constructor (see §4). Nothing else in the codebase reads an enabled flag.

---

## 4. Application wiring

Phase 1-2 wiring is fully automated by `assemble_skill_system()` in `src/donna/cli_wiring.py:300-470`, invoked from the CLI startup path. No manual steps required. Historical manual steps archived in [skill-system-history.md](../archive/skill-system-history.md).

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
  1. The dispatcher wasn't reconstructed with the new params (§4).
  2. `skill_routing_enabled=False` — double-check the config plumbing.
  3. The challenger passed to `agents["challenger"]` is the old no-arg constructor — it won't have `match_and_extract`.

### CapabilityMatcher matches the wrong capability

- Expected in early use. Tune `match_confidence_high` and `match_confidence_medium` in `SkillSystemConfig` based on observed match scores. Log entry `capability_match` emits `best_score` for every match attempt — use those to calibrate.

### Seed skills not loading into DB

- Symptom: `initialize_skill_system` runs but `skill` table remains empty.
- Check: does `skills/<capability_name>/skill.yaml` exist for each seed capability? The loader skips skills whose capability isn't in the registry. If a skill's capability_name doesn't match exactly, it's silently skipped (logged as `skill_skipped_no_capability`).

---

## 8. Quick activation checklist

- [ ] Dependencies installed (`pip install -e .` — pulls `sentence-transformers`, `numpy`, `httpx`)
- [ ] Database migration run (`alembic upgrade head`) — applies all 4 new migrations
- [ ] Four seed capabilities verified in DB (including `fetch_and_summarize`)
- [ ] All 7 new tables exist (capability, skill, skill_version, skill_state_transition, skill_run, skill_step_result, skill_fixture)
- [ ] `initialize_skill_system(db_conn, Path("skills"))` wired into application startup and its returned `ToolRegistry` captured (§4)
- [ ] `AgentDispatcher` constructor updated with `skill_executor`, `skill_database`, `skill_routing_enabled=True` (§4)
- [ ] `SkillExecutor` constructed with `tool_registry` (from startup), `triage`, and `run_repository` (§4)
- [ ] `ChallengerAgent` constructed with `matcher` and `input_extractor` (§4)
- [ ] App restarted
- [ ] `/admin/capabilities` returns four rows
- [ ] `/admin/skills` returns four sandbox skills
- [ ] `/admin/skill-runs` returns an empty list initially
- [ ] Sending a real task produces `dispatcher_skill_shadow_*` and `skill_step_completed` log events
- [ ] A `skill_run` row appears in the DB after each matched message
- [ ] User-facing behavior unchanged
