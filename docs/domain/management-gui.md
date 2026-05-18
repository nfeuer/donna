# Donna Management GUI

Developer-facing control panel for monitoring, debugging, and configuring the Donna AI assistant system.

**Separate from the end-user Flutter app** — this is the development/ops tool for understanding agent behavior, tracking costs, iterating on prompts/configs, and debugging data flows.

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Frontend | React 18 + Vite + TypeScript | SPA with client-side routing |
| Components | Ant Design 5 (dark theme) | Full admin component library |
| Charts | Recharts | Time series, bar, area, pie charts |
| HTTP Client | Axios | Proxied through Vite in dev |
| Backend | FastAPI (existing app, extended) | New `/admin/*` routes on port 8200 |
| Deployment | Docker (nginx) + compose | Port 8400 in production |
| Dev server | Vite | Port 5173, proxies to FastAPI |

## Architecture

```
donna-ui (React SPA, port 5173/8400)
    │
    ├── /admin/* ──► FastAPI (port 8200)
    │                   ├── SQLite (donna_tasks.db)
    │                   ├── Loki HTTP API (port 3100)
    │                   └── Config/prompt files (disk)
    │
    └── Static assets (nginx in production)
```

The GUI communicates exclusively through the FastAPI admin API. No direct database or Loki access from the frontend.

## Admin API Endpoints

All endpoints are under the `/admin` prefix. No authentication required today — this is a local dev tool bound to the loopback interface.

**If `/admin/*` is ever exposed externally**, the minimum bar would be:

1. A shared-secret bearer token (or Cloudflare Access / Tailscale header) enforced via a single FastAPI dependency — `Depends(require_admin)` — applied at the router layer so every `admin_*` router inherits it in one place.
2. Write-side endpoints (`PUT /admin/configs/{filename}`, `POST /admin/skills/{id}/state`, automation create/update/delete, `POST /admin/skill-runs/{id}/capture-fixture`) rate-limited more aggressively than read endpoints, and logged to `invocation_log` with caller identity.
3. No new persistence — reuse the existing access/auth infrastructure in `src/donna/api/routes/admin_access.py` (IP / device / caller tables) rather than adding a parallel admin-user table.

This is a note, not a plan — implementation is deferred until the tool leaves the loopback.

### Dashboard KPIs
| Endpoint | Description |
|----------|-------------|
| `GET /admin/dashboard/parse-accuracy?days=30` | Parse accuracy over time from correction_log vs invocation_log |
| `GET /admin/dashboard/agent-performance?days=30` | Per-agent call counts, latency, cost, success rates |
| `GET /admin/dashboard/task-throughput?days=30` | Tasks created vs completed, status distribution, overdue count |
| `GET /admin/dashboard/cost-analytics?days=30` | Daily/monthly spend, by task_type, by model, projections |

### Log Viewer
| Endpoint | Description |
|----------|-------------|
| `GET /admin/logs?event_type=&level=&service=&search=&start=&end=&limit=50&offset=0` | Paginated log query via Loki with fallback to invocation_log |
| `GET /admin/logs/trace/{correlation_id}` | All events for a correlation ID (trace timeline) |
| `GET /admin/logs/event-types` | Static event type hierarchy for tree filter |

### Invocations
| Endpoint | Description |
|----------|-------------|
| `GET /admin/invocations?task_type=&model=&is_shadow=&limit=50&offset=0` | Paginated invocation log |
| `GET /admin/invocations/{id}` | Single invocation with full output JSON |

### Tasks (Admin)
| Endpoint | Description |
|----------|-------------|
| `GET /admin/tasks?status=&domain=&priority=&search=&limit=50&offset=0` | Extended task list with agent/nudge/quality fields |
| `GET /admin/tasks/{id}` | Full task detail + linked invocations, nudges, corrections, subtasks |

### Configs & Prompts
| Endpoint | Description |
|----------|-------------|
| `GET /admin/configs` | List YAML config files with metadata |
| `GET /admin/configs/{filename}` | Read config file content |
| `PUT /admin/configs/{filename}` | Write config file (validates YAML, atomic write) |
| `GET /admin/prompts` | List prompt template files |
| `GET /admin/prompts/{filename}` | Read prompt file content |
| `PUT /admin/prompts/{filename}` | Write prompt file (atomic write) |

### Agents
| Endpoint | Description |
|----------|-------------|
| `GET /admin/agents` | List all agents with config + summary metrics from invocation_log |
| `GET /admin/agents/{name}` | Detailed agent view: config, recent invocations, daily latency, tool usage, cost summary |

### Shadow Scoring
| Endpoint | Description |
|----------|-------------|
| `GET /admin/shadow/comparisons?task_type=&days=30&limit=50` | Pair primary and shadow invocations by input_hash or task_id proximity |
| `GET /admin/shadow/stats?days=30` | Aggregate shadow vs primary quality and cost stats |
| `GET /admin/shadow/spot-checks?limit=50&offset=0` | Invocations flagged for review (spot_check_queued or quality < 0.7) |

### Preferences
| Endpoint | Description |
|----------|-------------|
| `GET /admin/preferences/rules?enabled=&rule_type=&limit=50` | List learned preference rules with filters |
| `PATCH /admin/preferences/rules/{id}` | Toggle rule enabled/disabled state |
| `GET /admin/preferences/corrections?field=&task_type=&limit=50&offset=0` | Paginated correction log |
| `GET /admin/preferences/stats` | Aggregate preference and correction statistics |

### Claude Inspector
| Endpoint | Description |
|----------|-------------|
| `GET /admin/claude/calls?task_type=&model=&date_from=&date_to=&min_cost=&min_tokens_in=&quality_score_below=&sort=&sort_dir=&limit=25&offset=0` | Paginated call browser with filters |
| `GET /admin/claude/calls/{invocation_id}/payload` | Full request/response JSON from disk |
| `GET /admin/claude/insights?days=7` | Computed waste-pattern insights (cost centers, prompt duplication, quality-cost mismatches, token bloat) |

### Escalations
| Endpoint | Description |
|----------|-------------|
| `GET /admin/escalations?status=&limit=50&offset=0` | List escalations with status filter |
| `GET /admin/escalations/{correlation_id}` | Escalation detail with full prompt, timeline, validation results |
| `POST /admin/escalations/{correlation_id}/submit` | Submit chat-mode answer for an open escalation |
| `POST /admin/escalations/{correlation_id}/validate` | Validate a submitted answer |

### Escalation Settings
| Endpoint | Description |
|----------|-------------|
| `GET /admin/escalation-settings` | All escalation settings with current values and slider cap |
| `PUT /admin/escalation-settings/{key:path}` | Update a single setting (optimistic locking via `expected_updated_at`) |
| `PUT /admin/escalation-settings/task-types/{task_type}` | Set per-task-type override (Auto / Force-API / Force-Manual / Disabled) |

### LLM Gateway
| Endpoint | Description |
|----------|-------------|
| `GET /admin/llm/analytics?days=7` | Per-caller analytics, queue stats, health data |
| `GET /admin/llm/queue/{item_id}/prompt` | Queue item prompt preview |

### Vault
| Endpoint | Description |
|----------|-------------|
| `GET /admin/vault/status` | Vault stats (note count, total size, last commit) |
| `GET /admin/vault/notes?folder=` | List notes with optional folder filter |
| `GET /admin/vault/notes/{path}` | Read a single note with frontmatter |
| `GET /admin/vault/history?limit=50` | Git commit history |

### Health
| Endpoint | Description |
|----------|-------------|
| `GET /admin/health` | Admin health check (DB, services, queue status) |

## Pages

### Core Pages

| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | 4 KPI sections: Parse Accuracy, Agent Performance, Task Throughput, Cost Analytics |
| `/chat` | Chat | Conversational interface with session list, message thread, context meter, and escalation support |
| `/calendar` | Calendar | Weekly calendar grid showing tasks and calendar events. Week navigation, refresh. Uses `fetchCalendarWeek` API |
| `/vault` | Vault | Obsidian vault browser — note list by folder, note viewer with markdown rendering, commit history panel. Stats cards |
| `/logs` | Log Viewer | Event type tree filter, level/service filters, correlation trace drawer, entity links |

### Configuration & Editing Pages

| Route | Page | Description |
|-------|------|-------------|
| `/configs` | Config Editor | Structured form editing for agents/models/task_types/states YAML. Raw YAML tab with Monaco editor. Diff view before save |
| `/prompts` | Prompt Editor | Monaco markdown editor with live preview. Template variable inspector. Schema link from task_types.yaml |

### Agent & Task Pages

| Route | Page | Description |
|-------|------|-------------|
| `/agents` | Agent Details | Card grid with metrics. Detail view: config, latency chart, tool usage chart, cost summary, recent invocations |
| `/tasks` | Task Browser | Filterable table (status/domain/priority/agent/search). Detail view with state timeline, linked invocations/nudges/corrections/subtasks |

### Model & Evaluation Pages

| Route | Page | Description |
|-------|------|-------------|
| `/shadow` | Shadow Scoring | Side-by-side diff of primary vs shadow model outputs. Quality score scatter plot + trend chart. Spot-check queue. Filter by task_type/days |
| `/llm-gateway` | LLM Gateway | Health dashboard, per-caller analytics with bar charts, live queue stream, queue item prompt preview. Range selector (7/14/30d) |
| `/claude` | Claude Inspector | LLM call forensics dashboard. Insights panel (top cost centers, prompt duplication, quality-cost mismatches, token bloat outliers). Sortable/filterable call browser with expandable detail view showing full request/response JSON. Side-by-side compare view. Deep-link support via URL query params (`?task_type=X&id=Y`). Payload data stored on filesystem with 1GB FIFO eviction |

### Preference & Escalation Pages

| Route | Page | Description |
|-------|------|-------------|
| `/preferences` | Preference Manager | Learned rules table with confidence scores, enable/disable toggle. Correction history with filters. Rule provenance drawer. Stats cards |
| `/escalations` | Escalations | List view of open/resolved escalations with status filter. Detail view per `correlation_id` with full prompt, chat-mode answer textarea, "Mark as built" modal, status timeline, validation result panel. Supports `tool_request_fulfillment` rows |
| `/escalation-settings` | Escalation Settings | Master kill switch, per-mode toggles (chat / claude_code), budget-extension allow + max-daily slider, per-task-type override grid (Auto / Force-API / Force-Manual / Disabled). Optimistic locking with 409 conflict handling |

### Skill System Pages

| Route | Page | Description |
|-------|------|-------------|
| `/skill-system` | Skill System | Tabbed view: Skills, Candidates, Drafts, Runs, Automations. GPU status card. Skill drawer, candidate expander, run drawer, automation drawer. State transition form |

### Dev Tools

| Route | Page | Description |
|-------|------|-------------|
| `/dev/primitives` | Dev Primitives | Component storybook — showcases all design primitives (Button, Card, Pill, Input, Select, Checkbox, Switch, Tabs, Tooltip, Dialog, Drawer, DropdownMenu, Popover, Skeleton, DataTable, etc.) |

### UX Features (Cross-cutting)
- Keyboard shortcuts (`g`+key nav, `r` refresh, `Esc` close)
- Saved filter presets (Log Viewer)
- CSV export on all tables
- Anomaly toast notifications on dashboard refresh

## Design Decisions Made

1. **Separate admin API prefix** (`/admin/*`) rather than a separate FastAPI service — keeps deployment simple, shares DB connection, avoids service sprawl.
2. **No auth on admin routes** — this is a local dev tool. Can add admin auth later if exposed externally.
3. **Ant Design over shadcn/ui** — richer out-of-the-box admin components (tables, trees, drawers, forms).
4. **Loki as primary log source** with SQLite fallback — structured logs already flow to Loki via Promtail. Direct SQLite queries cover invocation_log when Loki is down.
5. **Manual refresh + 30s auto-refresh on dashboard** — avoids WebSocket complexity for a dev tool.
6. **pnpm** as package manager — fast, disk-efficient.
7. **Dark theme only** — this is a developer tool, dark mode is the default.

## Frontend Project Structure

```
donna-ui/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tsconfig.node.json
├── index.html
├── Dockerfile
├── nginx.conf
├── src/
│   ├── main.tsx                          # App entry, ConfigProvider + BrowserRouter
│   ├── App.tsx                           # Route definitions
│   ├── vite-env.d.ts
│   ├── theme/
│   │   └── darkTheme.ts                  # Ant Design dark theme config
│   ├── api/
│   │   ├── client.ts                     # Axios instance with error handling
│   │   ├── dashboard.ts                  # Dashboard KPI fetchers
│   │   ├── logs.ts                       # Log query fetchers
│   │   ├── invocations.ts               # Invocation log fetchers
│   │   ├── configs.ts                    # Config/prompt CRUD fetchers
│   │   ├── agents.ts                     # Agent list/detail fetchers
│   │   ├── tasks.ts                      # Task list/detail fetchers
│   │   ├── shadow.ts                     # Shadow scoring fetchers
│   │   ├── preferences.ts               # Preference rules/corrections fetchers
│   │   ├── claude.ts                     # Claude Inspector: calls, payloads, insights
│   │   ├── calendar.ts                   # Calendar week fetcher
│   │   ├── chat.ts                       # Chat sessions, messages, context, escalation
│   │   ├── escalations.ts               # Escalation list/detail/submit/validate
│   │   ├── escalationSettings.ts        # Escalation settings GET/PUT, task-type overrides
│   │   ├── health.ts                     # Admin health check
│   │   ├── llmGateway.ts                # LLM gateway analytics, queue item prompts
│   │   ├── promptStats.ts               # Prompt statistics
│   │   ├── skillSystem.ts               # Skills, candidates, drafts, runs, automations, transitions
│   │   └── vault.ts                      # Vault notes, status, history
│   ├── components/
│   │   ├── Layout.tsx                    # App shell: sidebar + header + content
│   │   ├── PageShell.tsx                 # Placeholder for unbuilt pages
│   │   └── RefreshButton.tsx             # Manual refresh with "updated X ago"
│   ├── hooks/
│   │   └── useKeyboardShortcuts.ts       # Global keyboard nav (g+key, r refresh, Esc close)
│   ├── utils/
│   │   └── csvExport.ts                  # CSV export utility for tables
│   └── pages/
│       ├── Dashboard/
│       │   ├── index.tsx                 # 2x2 grid layout, time range selector
│       │   ├── ParseAccuracyCard.tsx     # Accuracy %, corrections breakdown
│       │   ├── AgentPerformanceCard.tsx  # Per-agent metrics, latency chart
│       │   ├── TaskThroughputCard.tsx    # Created vs completed, status pie
│       │   └── CostAnalyticsCard.tsx     # Spend charts, budget progress bar
│       ├── Logs/
│       │   ├── index.tsx                 # Two-panel layout, URL filter state
│       │   ├── EventTypeTree.tsx         # Checkbox tree sidebar filter
│       │   ├── LogTable.tsx              # Paginated, expandable, color-coded
│       │   └── TraceView.tsx             # Correlation trace drawer/timeline
│       ├── Configs/
│       │   ├── index.tsx                 # Layout with sidebar + structured/raw tabs
│       │   ├── ConfigFileList.tsx        # Sidebar file menu
│       │   ├── RawYamlEditor.tsx         # Monaco YAML editor
│       │   ├── StructuredEditor.tsx      # Routes to form by filename
│       │   ├── SaveDiffModal.tsx         # Monaco diff before save
│       │   └── forms/
│       │       ├── AgentsForm.tsx        # Agent cards with tools/autonomy
│       │       ├── ModelsForm.tsx        # Model defs, routing, cost, quality
│       │       ├── TaskTypesForm.tsx     # Collapsible task type editor
│       │       └── StatesForm.tsx        # SVG state diagram + transitions table
│       ├── Prompts/
│       │   ├── index.tsx                 # Split editor + preview layout
│       │   ├── PromptFileList.tsx        # Sidebar file menu
│       │   ├── VariableInspector.tsx     # Jinja2 variable extractor
│       │   └── MarkdownPreview.tsx       # Simple markdown renderer
│       ├── Agents/
│       │   ├── index.tsx                 # Card grid + detail toggle
│       │   ├── AgentCard.tsx             # Summary card with metrics
│       │   └── AgentDetail.tsx           # Config, charts, invocations
│       ├── Tasks/
│       │   ├── index.tsx                 # Filter bar + table
│       │   ├── TaskFilters.tsx           # Status/domain/priority/search
│       │   ├── TaskTable.tsx             # Paginated table with color tags
│       │   └── TaskDetail.tsx            # Full detail with state timeline
│       ├── Shadow/
│       │   ├── index.tsx                 # Filter controls, stats cards, tabbed layout
│       │   ├── ComparisonTable.tsx        # Side-by-side diff, expandable rows
│       │   ├── SpotCheckTable.tsx         # Flagged invocations with quality bars
│       │   └── ShadowCharts.tsx           # Scatter plot + trend line chart
│       ├── Preferences/
│       │   ├── index.tsx                  # Stats cards, rules/corrections tabs
│       │   ├── RulesTable.tsx             # Rules with confidence bar, enable toggle
│       │   ├── CorrectionsTable.tsx        # Paginated correction history
│       │   └── RuleDetailDrawer.tsx        # Rule provenance with supporting corrections
│       ├── Calendar/
│       │   ├── index.tsx                  # Week navigation, event/task fetch
│       │   ├── CalendarGrid.tsx           # Weekly grid layout
│       │   └── CalendarGrid.module.css    # Grid styles
│       ├── Chat/
│       │   ├── index.tsx                  # Session management, message send/receive
│       │   ├── SessionList.tsx            # Sidebar session list
│       │   ├── MessageThread.tsx          # Message display area
│       │   ├── MessageInput.tsx           # Input with submit
│       │   ├── ContextMeter.tsx           # Context window usage indicator
│       │   └── Chat.module.css            # Page styles
│       ├── Escalations/
│       │   ├── index.tsx                  # Status filter, list view
│       │   ├── EscalationsTable.tsx       # Escalation rows with status badges
│       │   ├── EscalationDetail.tsx       # Full prompt, answer textarea, timeline, validation
│       │   ├── MarkAsBuiltModal.tsx       # claude_code completion modal
│       │   └── Escalations.module.css     # Page styles
│       ├── EscalationSettings/
│       │   ├── index.tsx                  # Kill switch, toggles, slider, override grid
│       │   └── EscalationSettings.module.css # Page styles
│       ├── LLMGateway/
│       │   ├── index.tsx                  # Health + analytics + live queue stream
│       │   └── LLMGateway.module.css      # Page styles
│       ├── Vault/
│       │   ├── index.tsx                  # Folder browser, stats, note/commit tabs
│       │   ├── NoteViewer.tsx             # Markdown note renderer
│       │   ├── CommitHistory.tsx          # Git commit log display
│       │   └── Vault.module.css           # Page styles
│       ├── SkillSystem/
│       │   ├── index.tsx                  # Tabbed layout: skills/candidates/drafts/runs/automations
│       │   ├── SkillsTab.tsx              # Skills list
│       │   ├── SkillDrawer.tsx            # Skill detail drawer
│       │   ├── SkillDetailPanel.tsx       # Skill metadata panel
│       │   ├── SkillDetailPanel.module.css
│       │   ├── CandidatesTab.tsx          # Candidate list
│       │   ├── CandidateExpander.tsx      # Candidate detail expander
│       │   ├── CandidateExpander.module.css
│       │   ├── DraftsTab.tsx              # Draft list
│       │   ├── RunsTab.tsx               # Run history
│       │   ├── RunDrawer.tsx             # Run detail drawer
│       │   ├── AutomationsTab.tsx        # Automation list
│       │   ├── AutomationDrawer.tsx      # Automation detail drawer
│       │   ├── GpuStatusCard.tsx         # GPU health/status display
│       │   ├── StateTransitionForm.tsx   # Skill state transition form
│       │   └── SkillSystem.module.css    # Page styles
│       ├── DevPrimitives/
│       │   ├── index.tsx                  # Component storybook with all primitives
│       │   ├── StorySection.tsx           # Section wrapper for stories
│       │   └── DevPrimitives.module.css   # Page styles
│       └── ClaudeInspector/
│           ├── index.tsx                  # Insights fetch + filter state, URL param sync
│           ├── InsightsPanel.tsx           # 4-card grid: cost, duplication, mismatch, bloat
│           ├── CallBrowser.tsx             # Filter bar, sortable table, pagination
│           ├── CallDetail.tsx              # Expandable request/response JSON viewer
│           ├── CallCompare.tsx             # Side-by-side payload comparison
│           └── claude-inspector.module.css # Page styles
```

## Backend Files

```
src/donna/api/routes/
├── admin_dashboard.py            # 4 dashboard KPI endpoints
├── admin_logs.py                 # Log query + trace endpoints
├── admin_invocations.py          # Invocation log browse/detail
├── admin_tasks.py                # Extended task list/detail
├── admin_config.py               # Config/prompt file CRUD (read + write)
├── admin_agents.py               # Agent list/detail with merged metrics
├── admin_shadow.py               # Shadow scoring comparisons, stats, spot-checks
├── admin_preferences.py          # Preference rules CRUD, corrections, stats
├── admin_claude.py               # Claude Inspector: call browser, payload retrieval, insights
├── admin_escalations.py          # Escalation workspace: list, detail, submit, validate
├── admin_escalation_settings.py  # Escalation settings: kill switch, toggles, overrides
├── admin_health.py               # Admin health check endpoint
├── admin_llm.py                  # LLM gateway analytics, queue status
├── admin_vault.py                # Vault notes, status, history
├── admin_access.py               # IP / device / caller tables (auth infrastructure)
├── calendar_week.py              # Calendar week endpoint for UI grid
├── chat.py                       # Chat sessions, messages, context status, escalation
├── schedule.py                   # Schedule management
├── skills.py                     # Skill CRUD
├── skill_candidates.py           # Skill candidate management
├── skill_drafts.py               # Skill draft management
├── skill_runs.py                 # Skill run history and capture
├── automations.py                # Automation CRUD
├── llm.py                        # LLM queue and routing (non-admin)
├── capabilities.py               # Capability listing
├── auth_flow.py                  # OAuth flow endpoints
├── health.py                     # General health check
├── agents.py                     # Agent activity SQL patterns
└── tasks.py                      # Task CRUD (non-admin)

src/donna/collection/
├── payload_writer.py             # Fire-and-forget JSON writer for LLM request/response payloads
└── payload_evictor.py            # FIFO eviction: deletes oldest date dirs when over 1GB cap

src/donna/insights/
└── engine.py                     # SQL-based insights: cost centers, prompt groups, mismatches, bloat
```

## Reused Existing Code

| Class/Module | File | Used For |
|-------------|------|----------|
| `CostTracker` | `src/donna/cost/tracker.py` | Cost analytics dashboard queries |
| `Database` / `TaskRow` | `src/donna/tasks/database.py` | Task queries, field definitions |
| `InvocationMetadata` | `src/donna/logging/invocation_logger.py` | Schema reference for invocation queries |
| `load_*_config()` | `src/donna/config.py` | Reading YAML config files |
| Agent activity patterns | `src/donna/api/routes/agents.py` | SQL query patterns for invocation_log |

## Docker Deployment

| Service | Port | Compose File |
|---------|------|-------------|
| donna-ui | 8400 | `docker/donna-ui.yml` |
| donna-api | 8200 | `docker/donna-app.yml` (existing) |
| donna-loki | 3100 | `docker/donna-monitoring.yml` (existing) |

## Build History

All sessions below are complete. The page list above in [Pages](#pages) is the authoritative reference for what exists today.

### Session 1 — Dashboard + Log Viewer
Backend admin API routes (5 files), frontend scaffold (React + Vite + Ant Design dark theme), API client layer, Dashboard (4 KPI cards), Log Viewer, page shells, Docker files.

### Session 2 — Config/Prompt Editors, Agents, Tasks
Config editor (structured YAML + raw YAML + diff modal), Prompt editor (Monaco + live preview + variable inspector), Agent detail views, Task browser with state timeline.

### Session 3 — Shadow Scoring, Preferences, UX Polish
Shadow scoring comparison view, Preference rules manager, keyboard shortcuts, filter presets, CSV export, anomaly notifications.

### Session 4 — Claude Inspector
LLM call forensics dashboard with insights panel, call browser, payload viewer, compare view. PayloadWriter + PayloadEvictor backend.

### Post-Session — Additional Pages
Calendar, Chat, Escalations, EscalationSettings, LLMGateway, Vault, SkillSystem, DevPrimitives added across subsequent slices (slices 12, 15, 19, 22, 23, and others). Backend routes expanded from 9 to 30 files.

## Manual Escalation Surfaces (slices 19 + 22 + 23)

Three dashboard surfaces are defined by
[`docs/superpowers/specs/manual-escalation.md`](../superpowers/specs/manual-escalation.md):

1. **Escalation workspace** at `/admin/escalations` — list view of all
   open and resolved escalations, plus a detail view per `correlation_id`
   that renders the full prompt, hosts the chat-mode answer textarea or
   the claude_code "Mark as built" modal, and shows a status timeline +
   validation result panel. This is the canonical surface for full
   prompts and answer submission; Discord is the alert layer only.
   Slice 22 added `task_type='tool_request_fulfillment'` rows: the same
   detail view, but the spec body comes from
   `prompts/escalation/tool_build.md` and the validation panel renders
   tool-lint failures (`(lint:anthropic_import)`,
   `(lint:secrets:…)`, etc.) plus the optional
   `requires_rebuild_warning`. Lands in
   `slice_19_dashboard_escalation_workspace.md`; tool-build row support
   lands in `slice_22_tool_gap_surfacing.md`.
2. **Escalation Settings page (slice 23)** at `/escalation-settings` —
   master kill switch, per-mode toggles (chat / claude_code),
   budget-extension allow + max-daily slider, and a per-task-type
   override grid (`Auto / Force-API / Force-Manual / Disabled`). Backed
   by the `dashboard_setting` table; resolution order
   `dashboard_setting → YAML default`.
   - **API:** `GET /admin/escalation-settings`,
     `PUT /admin/escalation-settings/{key:path}`,
     `PUT /admin/escalation-settings/task-types/{task_type}`.
   - **Optimistic locking:** every PUT carries `expected_updated_at`
     from the most recent GET; the server returns 409 with the live
     state on a stale token (spec §10.7 row 1). The page surfaces a
     toast and replaces the stale value with the live state — no
     silent retry.
   - **Slider safety:** `max_daily_extension_usd` is server-validated
     against `hard_monthly_ceiling_usd / days_left_in_month`; the GET
     response carries the cap so the slider's max matches the PUT
     acceptance window.
   - **YAML-only ceiling:** `hard_monthly_ceiling_usd` is **not**
     dashboard-mutable — defense in depth so a compromised dashboard
     session cannot raise it (spec §10.7 row 4).
   - **Audit:** every successful write inserts an
     `escalation_lifecycle` row in `invocation_log` with
     `event='dashboard_setting_changed'` and a payload of `{key, value,
     previous_value, had_lock_token}`. `escalation_request_id` stays
     NULL because these are subsystem-level events; the slice 19
     per-row timeline filters on that FK and so only surfaces
     row-scoped events. Dashboard-setting changes are visible in the
     log viewer at `/admin/logs`.
3. **Tool gap queue** (future) — a dedicated list view of `tool_request`
   rows that would surface speculative gaps from the morning digest in
   a queryable form, plus snooze / file-request / reject controls.
   Slice 22 ships the data model and Discord ping; the standalone
   queue surface is not yet scheduled. For now, tool requests are
   visible as escalation-workspace rows of type
   `tool_request_fulfillment` (surface 1) and as dashboard-setting
   audit entries in `/admin/logs`.

All three follow the existing dashboard conventions described above.
