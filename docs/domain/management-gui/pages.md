# Pages & Surfaces

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

## UX Features (Cross-cutting)
- Keyboard shortcuts (`g`+key nav, `r` refresh, `Esc` close)
- Saved filter presets (Log Viewer)
- CSV export on all tables
- Anomaly toast notifications on dashboard refresh

## Manual Escalation Surfaces (slices 19 + 22 + 23)

Three dashboard surfaces are defined by
[`docs/superpowers/specs/manual-escalation.md`](../../superpowers/specs/manual-escalation.md):

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
3. **Tool gap queue** — data model and Discord ping shipped in slice 22. Standalone dashboard queue surface tracked as [G-17](../../superpowers/followups/open-backlog.md). Currently visible as escalation-workspace rows of type `tool_request_fulfillment` and as dashboard-setting audit entries in `/admin/logs`.

All three follow the existing dashboard conventions described above.
