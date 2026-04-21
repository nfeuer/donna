# Skill System — Open Backlog

**Date:** 2026-04-21
**Scope:** Only genuinely-open items. Closed/shipped follow-ups (F-1, F-2, F-3, F-5, F-6, F-7, F-9, F-10, F-11, F-14, F-W1-B–H, F-W2-B–G, F-W3-A–K, F-W4-B, C, D, F, G, I, J, K, L) and Wave-N Completed sections live in the archived historical tracker:
`docs/superpowers/followups/archive/2026-04-16-skill-system-followups.md`.

This doc is structured as **sequential waves** — pick one, do it, check in. Items under *Triggered* and *OOS* are deferred with explicit trigger conditions; don't build speculatively.

---

## Priority legend

- **P0** — Blocks `skill_system.enabled=true` in production.
- **P1** — Unlocks meaningful value; do after P0.
- **P2** — Deferred with a named trigger; don't build speculatively.
- **P3** — Exploratory / polish.

---

## Waves

### Wave 1 — Unblock Claude-native capability migration (Tool registration + F-13)  *(P1)*

**Goal:** Wire up the 4 Claude-native task-type capabilities seeded in `config/capabilities.yaml:100-135` so they stop running as `ad_hoc` and actually dispatch through the skill system.

**Why first:** Biggest concrete value, fully tractable backend work. The 4 capabilities (`generate_digest`, `prep_research`, `task_decompose`, `extract_preferences`) are inert today because the tools they reference are not in the registry at `src/donna/skills/tools/__init__.py:31-56` (currently only `web_fetch`, `rss_fetch`, `html_extract`, `gmail_search`, `gmail_get_message`).

**Scope:**
1. Implement 4 tools that wrap existing integrations (new files under `src/donna/skills/tools/`):
   - `calendar_read` → wraps `src/donna/integrations/calendar.py:32-71` (`GoogleCalendarClient`, `CalendarEvent`).
   - `task_db_read` → wraps `src/donna/tasks/database.py:39-100,200+` (`TaskRow`, `Database.get_task(s)`).
   - `cost_summary` → wraps `src/donna/cost/tracker.py:24-93` (`CostSummary`).
   - `email_read` → wraps `src/donna/integrations/gmail.py:50-80` (thin list-emails-by-criteria tool; distinct from existing `gmail_search` / `gmail_get_message`).
2. Register all 4 in `register_default_tools()` at `src/donna/skills/tools/__init__.py:31-56`, following the `functools.partial` client-injection pattern used for Gmail tools.
3. Add `tools:` fields to the 4 capabilities at `config/capabilities.yaml:100-135`:
   - `generate_digest` → `[calendar_read, task_db_read]`
   - `prep_research` → `[task_db_read]` (defer `web_search` to sub-item; still moves state off `ad_hoc`)
   - `task_decompose` → `[]` (pure reasoning; no tool dependencies)
   - `extract_preferences` → `[task_db_read]` (defer `notes_read`)
4. Per-tool unit tests following `tests/unit/test_skills_tools_web_fetch.py` pattern (mock the underlying async client).
5. Integration test: extend `tests/integration/test_cli_wires_tools_and_capabilities.py` to assert each of the 4 capabilities validates successfully under `SkillToolRequirementsLookup` (`src/donna/capabilities/tool_requirements.py:28-53`).

**Contract** (from `src/donna/skills/tool_registry.py:20`): each tool is `async def name(**kwargs) -> dict`, returns `{"ok": True, ...}` or raises a named `*Error`, logs via `structlog`.

**Sub-items deferred** (move to *Triggered* when this wave closes):
- `web_search` — trigger: productionize `prep_research` against real tasks.
- `notes_read` — trigger: a notes storage module exists (no backing module today).
- `fs_read` — trigger: a capability concretely needs read-only FS access.

**Acceptance:**
- All 4 tools registered and unit-tested.
- Integration test asserts all 4 capabilities pass `SkillToolRequirementsLookup`.
- `generate_digest`, `task_decompose` run end-to-end through the skill system (not ad_hoc).
- `prep_research` and `extract_preferences` pass validation even with deferred tools (their current YAML declares only what's available).

**Verification:**
```
pytest tests/unit/test_skills_tools_* tests/integration/test_cli_wires_tools_and_capabilities.py
```
Manually trigger `generate_digest` via CLI/Discord and confirm it runs through the skill system path (not ad_hoc fallback).

**Closes:** Tool-registration wave; F-13 (partial — `prep_research` / `extract_preferences` fully migrate once deferred tools land).

---

### Wave 2 — F-W1-A verification only (no fix)  *(P2, verify-then-defer)*

**Goal:** Confirm and *document* the DegradationDetector gap; add a regression test that reproduces it. No behavior change.

**Why:** Exploration confirmed the concern is real. `src/donna/skills/degradation.py:99,116` binarizes continuous `overall_agreement` scores via `degradation_agreement_threshold` before running Wilson CI on the binomial successes (`lines 101-103, 118-120`). The trigger `current_upper < baseline_agreement` (`lines 123-139`) cannot catch gradual drift when the baseline is already low — a skill sliding 0.90 → 0.60 with baseline 0.50 may never flag. The "correction-cluster fast path + EOD digest" workaround masks this in practice.

**Scope:**
1. Add a new test to `tests/unit/test_skills_degradation.py` named `test_degradation_misses_mid_drift_documented_gap` that reproduces the mid-drift miss: baseline ~0.50, divergences sliding 0.90 → 0.60 → 0.45 over the rolling window; assert detector does *not* flag.
2. Leave `src/donna/skills/degradation.py` unchanged.
3. Move F-W1-A from this wave into *Triggered* with a named trigger: *"A production skill exhibits the drift pattern the test documents."*

**Acceptance:**
- New test passes and encodes the gap.
- Backlog doc reflects the move to *Triggered*.

**Verification:**
```
pytest tests/unit/test_skills_degradation.py::test_degradation_misses_mid_drift_documented_gap
```

**Closes:** F-W1-A verification step. Fix deferred to *Triggered*.

---

### Wave 3 — F-12 Grafana skill-system panels  *(P2)*

**Goal:** Skill-specific observability dashboard built from existing Loki instrumentation (plus minimal additions).

**Scope:**
1. Verify + fill instrumentation gaps (emit only what's missing):
   - `skill_state_transition_logged` structlog event in `src/donna/skills/lifecycle.py` at each transition.
   - `skill_evolution_outcome` structlog event in `src/donna/skills/nightly.py` per evolution attempt (currently only aggregate `nightly_tasks_completed` is emitted).
2. Cost-per-skill strategy — adopt option (b): tag `task_type` with a `skill:<name>` prefix convention. No schema migration. Option (a) — add `skill_id` to `invocation_log` — stays available if (b) proves insufficient.
3. Create `docker/grafana/dashboards/skill_system.json` (template: copy layout from `docker/grafana/dashboards/task_pipeline.json`):
   - **Skill state distribution over time** — stacked timeseries on `event_type="skill_state_transition_logged" | to_state`.
   - **Evolution success rate per skill** — timeseries ratio of `outcome="success"` / total on `event_type="skill_evolution_outcome"`.
   - **Nightly-cron outcomes** — stat + log panel on existing `nightly_tasks_completed`.
   - **Cost breakdown by skill-system task type** — timeseries sum by `task_type` filtered on `skill:` prefix.
4. Smoke-test by loading the dashboard in local Grafana against the dev Loki stack.

**Acceptance:**
- All 4 panels populate against a local skill run that exercises each event type.
- Dashboard JSON is valid and imports cleanly.

**Verification:** Load dashboard, run a fake skill state transition and evolution, confirm panels populate within Loki retention.

**Closes:** F-12.

---

### Wave 4 — F-4 Dashboard UI for skill system + automations  *(P1, staged)*

**Goal this wave:** Record an approach decision so a follow-up plan can detail per-page work. **No code in this wave.**

**Why staged:** Biggest effort in the backlog. Backend routes already exist — UI is purely additive, but spans 5+ resource types (Skills, Candidates, Drafts, Runs, Automations, Evolution). Needs its own design cycle per original backlog note.

**Backend surface already in place:**
- `src/donna/api/routes/skills.py` — list/detail, state transitions, `requires_human_gate` flag toggle.
- `src/donna/api/routes/skill_candidates.py` — list, dismiss, draft-now.
- `src/donna/api/routes/skill_drafts.py` — list drafts.
- `src/donna/api/routes/skill_runs.py` — list runs, detail, divergence, capture-fixture.
- `src/donna/api/routes/automations.py` — full CRUD + pause/resume/run-now + run history.

**UI approaches (select one):**

- **Approach A — Five top-level pages** (Skills, Candidates, Drafts, Runs, Automations). Mirrors backend resource shape. Simple mental model, repetitive scaffolding, flat nav grows long.
- **Approach B — Nested "Skill System" hub with tabs + detail drawers** (Skills / Candidates / Drafts / Runs / Automations). One nav entry; tabs for resource type; drawer for detail (as in `LLMGateway`, `Shadow`). Lower cognitive load; matches existing dev-tool patterns.
- **Approach C — Lifecycle / pipeline view** (candidates → drafts → sandbox → shadow → trusted, automations separate). Matches state-machine mental model; harder to fit routine ops; risks obscuring cross-cutting views.

**Recommendation: Approach B.** Matches existing `Shadow` and `LLMGateway` patterns (see `donna-ui/src/pages/Shadow/index.tsx:49-73` and `donna-ui/src/pages/LLMGateway/index.tsx:1-43`); keeps nav compact; drawer-per-detail fits the single-user dev-tool posture documented in `docs/management-gui.md:36`.

**Follow-up plan (separate session) will cover:** route structure, per-tab columns/actions, drawer layouts, state-transition form (AS-3.3, AS-4.2, AS-4.3), `requires_human_gate` toggle, automation CRUD form, meta.* diagnostics surface (addresses F-W4-E), and test coverage.

**Acceptance:**
- An approach decision recorded here (update this doc when chosen).
- A follow-up plan exists for the detailed page breakdown.

**Verification:** Not applicable — this wave is a decision point.

**Closes:** F-4 design step. Pages ship in the follow-up plan. F-W4-E stays blocked on that follow-up.

---

### Wave 5 — Polish sweep  *(P3, as-convenient)*

**Goal:** Clear the low-priority items in one sitting.

**Scope:**
1. `config/dashboard.yaml:6` — evaluate + tune the TODO'd thresholds once more production data has accumulated.
2. `docs/management-gui.md:36` — add a note describing what auth *would* look like if `/admin/*` is ever exposed externally. No implementation.
3. Token counting — swap `len(prompt) // 4` heuristic for Ollama `/api/tokenize` call **only** when `context_overflow_escalation` rate > 10% (keep the trigger).
4. Slice 11 Flutter UI — leave a pointer to `slices/slice_11_flutter_ui.md`; it's a separate-repo track (`donna-app`) and doesn't live in this backlog.

**Acceptance:** Each item either resolved or explicitly deferred with its trigger restated.

**Verification:** `pytest` passes; updated files read cleanly.

---

## Triggered — don't build speculatively

| ID | Trigger | One-liner |
|---|---|---|
| **F-W2-A** | Second real capability ships and drift between migration blob and YAML matters | Log a diff when `SeedCapabilityLoader` overwrites migration-seeded rows, or make migration placeholder-only and let loader supply semantics. |
| **F-W3-E** | Concurrent-user scale (currently single user) | `AutomationConfirmationView.on_message` holds a 30-min timeout coroutine. Convert to fire-and-forget `asyncio.create_task` for scale. |
| **F-W4-A** | User asks to scan all inbound mail instead of sender allowlist | `email_triage` unbounded-sender mode — different privacy + token cost profile. |
| **F-W4-E** | After F-4 lands | Surface per-run `meta.*` diagnostics in the dashboard. |
| **F-W1-A fix** | A production skill exhibits the drift pattern the Wave 2 test documents | Add correction-cluster fast path + EOD digest mechanism; or replace Wilson CI on binarized scores with a continuous-score drift detector. |

---

## OOS items (deferred by original spec §2)

These were deliberately scoped out with explicit trigger conditions. Do not build without the trigger.

- **OOS-1** Event-triggered automations (`on_event`). *Trigger:* 3+ automations clearly need it.
- **OOS-2** Per-capability challenger runbooks. *Trigger:* 6 months of challenger-usage data.
- **OOS-3** Automation composition (chains / DAG). *Trigger:* real use case.
- **OOS-4** Step-level shadow comparison. *Trigger:* evolution quality poor across 5+ skills.
- **OOS-5** Logprob-based confidence. *Trigger:* self-assessed confidence proves uncorrelated.
- **OOS-6** Multiple skills per capability. *Trigger:* demonstrated need for divergent implementations.
- **OOS-7** Automation sharing / templates. *Trigger:* second real user exists.
- **OOS-8** Auto `requires_human_gate` flagging from sensitive tools. *Trigger:* manual flagging misses.
- **OOS-9** If-conditionals in the skill DSL. *Trigger:* 3+ skills need branching.
- **OOS-10** Nested DSL primitives (`for_each` inside `for_each`). *Trigger:* a skill needs nesting that can't be flattened.
- **OOS-11** Exact tokenization for local context budgeting. *Trigger:* `context_overflow_escalation` rate > 10%.
- **OOS-12** Voice-triggered challenger interactions. *Trigger:* voice UX is prioritized.

See the archived tracker for full design notes on each OOS item.

---

## Summary table

| Wave | Bucket | Items | Priority |
|---|---|---|---|
| 1 | Tool registration + F-13 | 4 tools + 4 capability YAML updates | P1 |
| 2 | F-W1-A verification | Regression test only | P2 |
| 3 | F-12 Grafana skill panels | 2 log events + 1 dashboard | P2 |
| 4 | F-4 Dashboard UI (staged) | Decision + follow-up plan | P1 |
| 5 | Polish sweep | 4 small items | P3 |
| — | Triggered (deferred) | 5 items | P2 |
| — | OOS (triggered by spec) | 12 items | P2 |

Historical closed items: see the archived tracker.
