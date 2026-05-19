# Project Reference

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

All sessions below are complete. The page list in [Pages](pages.md) is the authoritative reference for what exists today.

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
