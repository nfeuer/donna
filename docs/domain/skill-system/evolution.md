# Evolution, Corrections & Automations

Phase 4 closes the evolution loop and wires everything into FastAPI lifespan. Phase 5 adds schedule-driven automations.

---

## Phase 4 — Evolution, Correction Clustering, Production Wiring

Phase 4 closes the evolution loop, adds correction-cluster fast-path flagging, and wires all components into the FastAPI lifespan via a single helper so the skill system is fully active when `config.enabled: true`.

---

### P4.1 New components

| Class | Module | Role |
|-------|--------|------|
| `Evolver` | `src/donna/skills/evolution.py` | Single-skill evolution attempt orchestrator. |
| `EvolutionInputBuilder` | `src/donna/skills/evolution_input.py` | Assembles the Claude input package (divergence cases, fixtures, current YAML). |
| `EvolutionGates` | `src/donna/skills/evolution_gates.py` | 4 validation gates (syntax, targeted-case pass rate, fixture regression, recent-success guard). |
| `EvolutionScheduler` | `src/donna/skills/evolution_scheduler.py` | Iterates all degraded skills and calls `Evolver` for each, respecting the daily cap. |
| `SkillEvolutionLogRepository` | `src/donna/skills/evolution_log.py` | Reads and writes `skill_evolution_log` rows. |
| `CorrectionClusterDetector` | `src/donna/skills/correction_cluster.py` | Fast-path: scans recent skill runs for user-correction clusters and flags skills directly to `flagged_for_review`. |
| `AsyncCronScheduler` | `src/donna/skills/crons/scheduler.py` | Fires `run_nightly_tasks` daily at `config.nightly_run_hour_utc` (default 3 AM UTC). Runs as an `asyncio` background task. |
| `assemble_skill_system` | `src/donna/skills/startup_wiring.py` | Lifespan helper that constructs and wires all Phase 3 + 4 components, returning a `SkillSystemBundle`. |

---

### P4.2 New config knobs

Add to `config/skills.yaml` (all under the top-level document, alongside Phase 3 knobs):

```yaml
# ── Phase 4 knobs ──────────────────────────────────────────────────────────────
evolution_min_divergence_cases: 15          # Min divergence cases needed to attempt evolution
evolution_max_divergence_cases: 30          # Cap on cases sent to Claude per evolution call
evolution_targeted_case_pass_rate: 0.80     # Gate 2: evolved skill must pass ≥ this fraction of targeted cases
evolution_fixture_regression_pass_rate: 0.95 # Gate 3: evolved skill must not regress existing fixtures below this
evolution_recent_success_count: 20          # Gate 4: min recent successes required before accepting evolution
evolution_recent_success_window_days: 30    # Gate 4: window (days) for the recent-success count
evolution_max_consecutive_failures: 2       # After this many consecutive failed evolution attempts, revert to claude_native
evolution_estimated_cost_usd: 0.75          # Estimated cost per evolution call (budget guard check)
evolution_daily_cap: 10                     # Max evolution attempts per nightly run
correction_cluster_window_runs: 10          # Number of recent runs to inspect for correction clusters
correction_cluster_threshold: 2             # Min corrections in the window to flag the skill
```

---

### P4.3 Production wiring (now active)

Setting `enabled: true` in `config/skills.yaml` now fully activates the skill system end-to-end. The application startup hook (`src/donna/api/__init__.py` FastAPI lifespan) calls `assemble_skill_system`, which:

- Constructs every Phase 3 and Phase 4 component and wires them together.
- Returns a `SkillSystemBundle` containing all objects.
- The bundle is attached to `app.state.skill_system_bundle`.
- `app.state.skill_lifecycle_manager` and `app.state.auto_drafter` are exposed individually for dashboard routes (unchanged from Phase 3).
- `AsyncCronScheduler.run_forever()` is started as an `asyncio.create_task` background task. It fires `run_nightly_tasks` daily at `config.nightly_run_hour_utc`.
- On application shutdown (lifespan teardown), the scheduler task is cancelled cleanly.

No manual wiring steps from §4 or §P3.3 are needed when using `assemble_skill_system` — it handles all of it.

---

### P4.4 Nightly execution order

The updated `run_nightly_tasks` runs five sub-jobs in the following order (spec §6.5 budget ordering). Each step is wrapped in `try/except` — one failing does not stop the others.

1. **`SkillCandidateDetector.run()`** — scan `skill_run` for high-frequency `claude_native` task types and create `skill_candidate_report` rows.
2. **`EvolutionScheduler.run()`** — attempt evolution for all degraded skills (priority over drafting, as evolution improves existing coverage rather than adding new cost).
3. **`AutoDrafter.run()`** — draft new skills from pending candidates (using remaining budget after evolution).
4. **`DegradationDetector.run()`** — compute Wilson-score confidence intervals over recent divergence rows and transition skills whose CI lower bound falls below threshold to `flagged_for_review`.
5. **`CorrectionClusterDetector.scan_once()`** — fast-path scan: flag skills where the correction count in the last `correction_cluster_window_runs` runs meets or exceeds `correction_cluster_threshold`.

---

### P4.5 Design Constraints

- **Sandbox executor for fixture validation.** Both `AutoDrafter` and `Evolver` accept an `executor_factory=None` parameter. When `None`, the fixture-validation gates return `pass_rate=1.0` (vacuous pass). Drafted and evolved skills land in `draft` state and require human approval before reaching `sandbox`, so the safety posture is unchanged.
- **Evolution transitions rest at `draft`.** The spec 6.2 state-transition table requires `human_approval` for `draft -> sandbox`. Auto-evolution cannot bypass that gate -- an evolved skill is never promoted past `draft` without a human in the loop.

---

## Phase 5 — Automation Subsystem

Ships schedule-driven Donna work (monitors, scheduled summaries, periodic checks) as first-class entities distinct from user to-do tasks.

### P5.1 New tables

- `automation` — recurring work definitions. Columns: id, user_id, name, capability_name, inputs (JSON), trigger_type, schedule (cron), alert_conditions (JSON), alert_channels (JSON), max_cost_per_run_usd, min_interval_seconds, status, last_run_at, next_run_at, run_count, failure_count, created_via.
- `automation_run` — per-execution log. Columns: id, automation_id, started_at, finished_at, status, execution_path (skill | claude_native), skill_run_id, invocation_log_id, output (JSON), alert_sent, alert_content, error, cost_usd.

Migration: `alembic/versions/add_automation_tables_phase_5.py` (revision `a7b8c9d0e1f2`).

### P5.2 New components

- `AutomationRepository` (`src/donna/automations/repository.py`) — sole persistence layer.
- `CronScheduleCalculator` (`src/donna/automations/cron.py`) — next_run_at arithmetic via `croniter`.
- `AlertEvaluator` (`src/donna/automations/alert.py`) — `alert_conditions` DSL (`all_of`, `any_of`, 8 ops, dotted paths).
- `AutomationDispatcher` (`src/donna/automations/dispatcher.py`) — executes one due automation end-to-end.
- `AutomationScheduler` (`src/donna/automations/scheduler.py`) — asyncio poll loop.

### P5.3 Config knobs

Added to `config/skills.yaml`:

```yaml
automation_poll_interval_seconds: 60
automation_min_interval_default_seconds: 300
automation_failure_pause_threshold: 5
automation_max_cost_per_run_default_usd: 2.0
```

### P5.4 Dependency

- `croniter>=2.0.0` (new) — pure-Python cron parser, no C extensions.

### P5.5 Wiring

When `skill_system.enabled = true`, the FastAPI lifespan instantiates:
- `AutomationRepository(db.connection)`
- `AutomationDispatcher(router, executor_factory=None, budget_guard, alert_evaluator, cron, notifier, config)`
- `AutomationScheduler(repo, dispatcher, poll_interval_seconds)`

The scheduler runs as an `asyncio.create_task(scheduler.run_forever())` alongside the existing `AsyncCronScheduler` from Phase 4. Shutdown calls `scheduler.stop()` + `task.cancel()`.

### P5.6 New API routes

All under `/admin/automations`:

- `GET /admin/automations` — list (filters: status, capability_name, limit, offset)
- `GET /admin/automations/{id}` — single detail
- `POST /admin/automations` — create
- `PATCH /admin/automations/{id}` — edit
- `POST /admin/automations/{id}/pause` — set status=paused
- `POST /admin/automations/{id}/resume` — set status=active + recompute next_run_at
- `DELETE /admin/automations/{id}` — soft delete (status=deleted)
- `POST /admin/automations/{id}/run-now` — dispatch immediately (manual + force-run)
- `GET /admin/automations/{id}/runs` — run history

### P5.7 Planned Enhancements

Event-triggered automations (OOS-1), automation composition (OOS-3), cross-user sharing (OOS-7), dashboard UI, and Discord natural-language creation are tracked in the [open backlog](../../superpowers/followups/open-backlog.md).
