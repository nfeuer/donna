# Lifecycle, Shadow Sampling & Auto-Drafting

Phase 3 components that turn seed skills from static sandbox entries into self-improving, auto-promoted skills.

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

**Important:** `ShadowSampler` is injected into `SkillExecutor`. After each successful trusted-skill run, the executor fires an `asyncio.create_task` (non-blocking) that calls `ShadowSampler.sample_if_applicable(...)`. The sampler respects `shadow_sample_rate_trusted` and will not slow down the hot path.

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
3. `DegradationDetector.run()` — computes Wilson-score confidence intervals over recent divergence rows and transitions any skill whose CI **upper** bound falls below the stored `baseline_agreement` to `flagged_for_review`. (Using the upper bound is the stricter test: a skill is only flagged when even the optimistic end of its current agreement interval sits below the baseline it earned at promotion.)

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

These routes are split across four route modules:

| Module | Scope |
|--------|-------|
| `src/donna/api/routes/skills.py` | Skill CRUD, state transitions, human-gate toggle |
| `src/donna/api/routes/skill_candidates.py` | Candidate listing, dismiss, draft-now |
| `src/donna/api/routes/skill_drafts.py` | Draft listing |
| `src/donna/api/routes/skill_runs.py` | Run and step-result queries, divergence |

All four routers are mounted on the FastAPI app via `include_router()` at startup.

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
