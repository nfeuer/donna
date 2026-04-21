# Wave 4 — Skill-System Dashboard UI

**Date:** 2026-04-21
**Unblocks:** F-4, F-W4-E
**Backlog entry:** `docs/superpowers/followups/open-backlog.md` §"Wave 4"

## Context

The skill system has full backend CRUD + lifecycle routes but no UI. Wave 4 of the skill-system follow-up backlog recorded the approach decision (Approach B + Dashboard card) and deferred the page-level breakdown to this plan.

Scope goal: surface every existing skill-system backend route in the admin UI so the user can observe, transition, and operate skills + automations without touching `curl` or the DB. Close F-4, and close F-W4-E by surfacing per-run diagnostics (`state_object` + `step_results`).

Two surfaces:
1. A `SkillSystemCard` on the main Dashboard — at-a-glance KPIs.
2. A dedicated `/skill-system` page — tabbed resource management with detail drawers.

## Convention anchors

Reused without modification. Do not introduce new patterns.

- `docs/management-gui.md` — canonical design doc. Cards on Dashboard, page-per-subsystem, `/admin/*` API, no auth, 30s auto-refresh, dark theme only.
- `donna-ui/src/pages/Dashboard/index.tsx` — card-grid convention. New card is a `*Card.tsx` next to `AgentPerformanceCard.tsx`.
- `donna-ui/src/pages/Shadow/index.tsx` — `PageHeader + filters + table + drawer` pattern.
- `donna-ui/src/pages/LLMGateway/index.tsx` — same pattern with a live-status strip on top.
- `donna-ui/src/primitives/` — reuse: `Drawer`, `DataTable`, `Tabs`, `PageHeader`, `Pill`, `Segmented`, `Select`, `Stat`, `Switch`, `Dialog`, `EmptyState`, `Skeleton`, `Button`, `Input`, `Tooltip`, `Popover`, `ScrollArea`.
- `donna-ui/src/layout/Sidebar.tsx` — register one new nav entry.
- `donna-ui/src/api/shadow.ts` and `donna-ui/src/api/preferences.ts` — template for the new `skillSystem.ts` fetcher module.

## Dashboard card

**File:** `donna-ui/src/pages/Dashboard/SkillSystemCard.tsx` (new, full-width slot per existing grid).

**Stats surfaced:**
- Count by state: `draft`, `sandbox`, `shadow_primary`, `trusted`, `flagged_for_review`, `degraded` (stacked pills).
- New-candidate count (24h).
- Evolution success rate (24h) — ratio of successful evolutions to attempts.
- Active automation count + automation failures (24h).

**Wiring:**
- Respects the existing Dashboard `days` time-range selector.
- Loading + error states mirror sibling cards (`Skeleton` + `EmptyState`).
- Anomaly toast parity: reuse the `Dashboard/index.tsx` toast helper for thresholds (e.g., new degraded skill, automation failure spike). Thresholds defined in `config/dashboard.yaml` alongside existing TODO thresholds (Wave 5).
- Click-through: card title links to `/skill-system`; each stat chip deep-links to the matching tab (`/skill-system?tab=skills&state=trusted`).

**Data source:** new aggregator endpoint, see "Backend deltas" below.

## `/skill-system` page shell

**Files:**
- `donna-ui/src/pages/SkillSystem/index.tsx`
- `donna-ui/src/pages/SkillSystem/SkillsTab.tsx`, `SkillDrawer.tsx`
- `donna-ui/src/pages/SkillSystem/CandidatesTab.tsx`, `CandidateDrawer.tsx`
- `donna-ui/src/pages/SkillSystem/DraftsTab.tsx` (drawer reuses `SkillDrawer`)
- `donna-ui/src/pages/SkillSystem/RunsTab.tsx`, `RunDrawer.tsx`
- `donna-ui/src/pages/SkillSystem/AutomationsTab.tsx`, `AutomationDrawer.tsx`
- `donna-ui/src/pages/SkillSystem/StateTransitionForm.tsx` (shared)
- `donna-ui/src/pages/SkillSystem/JsonViewer.tsx` *(only if no existing primitive; confirm during implementation)*

**Routing:**
- Add `<Route path="/skill-system" element={<SkillSystem />} />` to `donna-ui/src/App.tsx`.
- Add `{ path: "/skill-system", label: "Skill System", icon: <Network size={18} /> }` (or equivalent lucide icon) to `donna-ui/src/layout/Sidebar.tsx`.

**Page layout:**
- `<PageHeader eyebrow="Infrastructure" title="Skill System" actions={<RefreshButton />} />`.
- Horizontal `Tabs` strip: Skills | Candidates | Drafts | Runs | Automations.
- URL contract: `?tab=<name>&id=<resource_id>`. Selecting a row sets `id`; open drawer binds to `id`; closing drawer clears `id`. Tab switch clears `id`. Shareable links.
- Auto-refresh: 30s default per management-gui.md decision 5. Manual refresh button in header.

## Tabs

### Skills
- **List columns:** `capability_name`, `state` (Pill), `requires_human_gate` (icon), `baseline_agreement` (number to 3dp), `current_version_id`, `updated_at`.
- **Filters:** state dropdown (all / per-state), requires_human_gate toggle, capability_name search.
- **Row action:** click → drawer.
- **Drawer (`SkillDrawer.tsx`):**
  - Header: capability_name + state Pill + version badge.
  - Tabs inside drawer: *Overview* (baseline_agreement, flags, timestamps), *Version* (yaml_backbone, step_content, output_schemas, changelog, created_by), *Transitions* (`StateTransitionForm`), *Runs* (link that opens the Runs tab with `skill_id` filter).
  - `requires_human_gate` Switch with inline confirm Dialog for flips.
- **Backend:** `GET /skills`, `GET /skills/{id}`, `POST /skills/{id}/state`, `POST /skills/{id}/flags/requires_human_gate`.

### Candidates
- **List columns:** `capability_name`, `status` (Pill), `expected_savings_usd`, `volume_30d`, `variance_score`, `reported_at`.
- **Filters:** status dropdown (default "new").
- **Drawer actions:** Dismiss (confirmation Dialog) • Draft-now (fires 202; toast on acceptance).
- **Backend:** `GET /skill-candidates`, `POST /skill-candidates/{id}/dismiss`, `POST /skill-candidates/{id}/draft-now`.

### Drafts
- **Read-only projection** of skills where state = `draft` (mirrors `GET /skill-drafts` surface).
- Row click opens `SkillDrawer` (shared component). No separate drawer file needed.
- **Backend:** `GET /skill-drafts`.

### Runs — closes F-W4-E
- **List columns:** `skill_id` (link to Skills drawer), `status`, `total_cost_usd`, `total_latency_ms`, `started_at`, `escalation_reason`.
- **Filters:** status dropdown, skill_id (via URL), date range.
- **Drawer (`RunDrawer.tsx`):**
  - Summary strip: status, cost, latency, user_id, timestamps, error (if any).
  - Section: `state_object` rendered via `<JsonViewer />` (collapsible tree).
  - Section: `step_results` — one collapsible card per step showing step_name, step_kind, latency_ms, validation_status, tool_calls, output (JsonViewer), error.
  - Section: linked `divergence` row (overall_agreement, diff_summary, flagged_for_evolution) when present; link to Shadow page's comparison if cross-linkable.
  - Action: **Capture Fixture** button → `POST /skill-runs/{id}/capture-fixture`; toast returns fixture_id.
- **Backend:** `GET /skill-runs`, `GET /skills/{skill_id}/runs`, `GET /skill-runs/{id}`, `GET /skill-runs/{id}/divergence`, `POST /skill-runs/{id}/capture-fixture`.

### Automations
- **List columns:** `name`, `capability_name`, `trigger_type`, `schedule`, `status` (Pill), `next_run_at`, `run_count`, `failure_count`.
- **Filters:** status (default "active"), trigger_type.
- **Primary page action:** `+ New Automation` opens drawer in create mode.
- **Drawer (`AutomationDrawer.tsx`):**
  - Form fields: name, description, capability_name (select from available capabilities), inputs (JSON editor), trigger_type, schedule (cron input with client-side validation via `cron-parser`), alert_conditions, alert_channels (multi-select), max_cost_per_run_usd, min_interval_seconds.
  - Action buttons (edit mode only): Pause / Resume / Run-now / Delete.
  - Run history: expandable accordion showing recent rows from `/automations/{id}/runs`.
- **Backend:** `GET /automations`, `GET /automations/{id}`, `POST /automations`, `PATCH /automations/{id}`, `DELETE /automations/{id}`, `POST /automations/{id}/pause`, `POST /automations/{id}/resume`, `POST /automations/{id}/run-now`, `GET /automations/{id}/runs`.

## Shared primitives within the page

### `StateTransitionForm`
- Two `Select` dropdowns: `to_state` and `reason`. Optional `Textarea` for `notes`.
- Dropdown options filtered client-side using metadata from a new `GET /skills/_transitions` endpoint (see Backend deltas). Keeps the config as the single source of truth.
- Submit: `POST /skills/{id}/state` with `{ to_state, reason, notes }`. On success, drawer refetches the skill (baseline_agreement may reset per `flagged_for_review → trusted` rule).

### `JsonViewer`
- Collapsible tree for dicts / lists. If no primitive exists in `donna-ui/src/primitives/`, add one with minimal API (`<JsonViewer value={obj} />`). Same component reused for `state_object`, `step_results[i].output`, `inputs`, `tool_calls`, `diff_summary`.
- Fallback: `<pre>` with syntax-highlighted JSON via existing Monaco setup if Monaco is already bundled.

## API client layer

**File:** `donna-ui/src/api/skillSystem.ts` (new; sibling of `shadow.ts`, `preferences.ts`).

Sections:
- `skills.*` — list / get / transitionState / setHumanGate / listRuns / getTransitionsMetadata.
- `candidates.*` — list / dismiss / draftNow.
- `drafts.*` — list.
- `runs.*` — list / get / getDivergence / captureFixture.
- `automations.*` — list / get / create / update / delete / pause / resume / runNow / listRuns.
- `dashboard.getSkillSystem(days)` — feeds the Dashboard card.

Typed via TS interfaces mirroring the Pydantic response models in `src/donna/api/routes/skills.py` et al.

## Backend deltas

Only two new endpoints; everything else reuses what the backlog Wave 4 section already enumerates.

1. `GET /admin/dashboard/skill-system?days=N`
   - **New file:** add to an existing admin-dashboard router (e.g., `src/donna/api/routes/admin_dashboard.py` per `docs/management-gui.md:217`). Aggregates counts from `skill`, `skill_candidate`, `skill_run`, `automation` tables.
   - Returns: `{ by_state: {state: count}, new_candidates_24h, evolution_success_rate_24h, active_automations, automation_failures_24h }`.
2. `GET /skills/_transitions`
   - **New route** in `src/donna/api/routes/skills.py`. Returns the valid-transitions table derived from `config/task_states.yaml` via existing loader in `src/donna/skills/lifecycle.py`.
   - Shape: `{ transitions: [{ from_state, to_state, allowed_reasons: [...] }] }`.

Neither requires a schema migration.

## Scope cuts for v1

- **No kanban / pipeline view** (Approach C rejected in the Wave 4 decision).
- **No bulk actions** on any table.
- **No inline row editing** — all edits via drawer.
- **No WebSocket / SSE** — 30s auto-refresh per management-gui.md decision 5.
- **No auth** — single-user dev tool per `management-gui.md:36`.
- **No cross-skill fleet comparisons** — use the existing Shadow page for that.
- **No skill editor / YAML editing UI** — surface version content read-only; authoring happens via the existing Configs/Prompts editors.

## Test coverage

- **Backend unit tests:** two new endpoints (`/admin/dashboard/skill-system`, `/skills/_transitions`) with seeded fixtures.
- **Frontend component tests** per tab using the existing Vitest + Testing Library setup:
  - Row click sets URL `id` param and opens drawer.
  - `StateTransitionForm` dropdowns filter correctly per transition metadata.
  - `AutomationDrawer` validates cron expression; submit disabled on invalid.
  - `RunDrawer` renders `state_object` and `step_results` from a fixture.
- **Integration:** extend `tests/integration/` so the new aggregator endpoint produces the expected shape against a seeded skill-system state.
- **E2E (Playwright or equivalent, if set up):** one flow per resource — list → open drawer → primary action → close.

## Acceptance

- `SkillSystemCard` renders on the Dashboard with live data against the new aggregator endpoint.
- `/skill-system` page routes, renders all five tabs, drawers open/close, URL params drive selection.
- `StateTransitionForm` succeeds for each valid transition and shows inline validation for invalid combinations.
- Automation CRUD round-trips through the backend (create, edit, pause, resume, run-now, delete).
- F-W4-E closes: Runs drawer surfaces `state_object` and `step_results` per run.

## Verification

- `pnpm test` in `donna-ui/` passes (unit + component).
- `pytest tests/unit/test_admin_dashboard_skill_system.py tests/unit/test_skills_transitions_endpoint.py tests/integration/test_skill_system_ui_routes.py` (new files) passes.
- Manual smoke:
  1. Start the dev stack (`docker compose -f docker/donna-app.yml -f docker/donna-ui.yml up`).
  2. Seed a fixture containing: 1 skill per state, 2 candidates (1 new / 1 dismissed), 3 runs (1 per status) with `state_object` + `step_results` populated, 1 active automation with run history.
  3. Visit `/` — `SkillSystemCard` renders expected counts.
  4. Click through to `/skill-system` — exercise each tab, open each drawer, perform the primary action, confirm backend state changes (via logs or DB inspection).
  5. Bookmark a deep link (`/skill-system?tab=runs&id=<run_id>`) and reload — drawer opens to the same run.

## Open items to resolve during implementation

- **Icon choice** for sidebar entry (candidates: `Network`, `Workflow`, `BrainCircuit` from lucide-react).
- **Anomaly thresholds** for the Dashboard card — coordinate with Wave 5 item 1 (`config/dashboard.yaml:6`).
- **JsonViewer**: confirm whether an existing primitive suffices before adding a new one.
- **Automations `inputs` editor**: raw JSON textarea for v1; richer schema-driven form deferred.
