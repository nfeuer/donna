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

All endpoints are under the `/admin` prefix. No authentication required (development tool). Auth will be added in a future session if needed.

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

### Configs & Prompts (read-only in session 1, editable in session 2)
| Endpoint | Description |
|----------|-------------|
| `GET /admin/configs` | List YAML config files with metadata |
| `GET /admin/configs/{filename}` | Read config file content |
| `PUT /admin/configs/{filename}` | Write config file (session 2) |
| `GET /admin/prompts` | List prompt template files |
| `GET /admin/prompts/{filename}` | Read prompt file content |
| `PUT /admin/prompts/{filename}` | Write prompt file (session 2) |

## Pages

### Session 1 (Implemented)
| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | 4 KPI sections: Parse Accuracy, Agent Performance, Task Throughput, Cost Analytics |
| `/logs` | Log Viewer | Event type tree filter, level/service filters, correlation trace drawer, entity links |

### Session 2 (Planned)
| Route | Page | Description |
|-------|------|-------------|
| `/configs` | Config Editor | Edit agents.yaml, donna_models.yaml, task_types.yaml, task_states.yaml, preferences.yaml via structured forms. Hot reload support. |
| `/prompts` | Prompt Editor | Edit prompt markdown templates with live preview. Variable inspection (show Jinja2 template vars). Syntax highlighting. |
| `/agents` | Agent Details | Per-agent activity feed, performance charts, tool usage breakdown, execution timeline |
| `/tasks` | Task Browser | Filterable task table with state timeline, linked invocations/nudges/corrections, subtask tree |

### Session 3 (Planned)
| Route | Page | Description |
|-------|------|-------------|
| `/shadow` | Shadow Scoring | Side-by-side diff of primary vs shadow model outputs. Quality score comparison. Filter by task_type. |
| `/preferences` | Preference Manager | View learned rules with confidence scores. Correction history. Enable/disable rules. Manual rule creation. |
| Polish | UX Refinements | Keyboard shortcuts, saved filter presets, export to CSV, notification toasts for anomalies |

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
│   │   └── invocations.ts               # Invocation log fetchers
│   ├── components/
│   │   ├── Layout.tsx                    # App shell: sidebar + header + content
│   │   ├── PageShell.tsx                 # Placeholder for unbuilt pages
│   │   └── RefreshButton.tsx             # Manual refresh with "updated X ago"
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
│       ├── Configs/index.tsx             # Shell (session 2)
│       ├── Prompts/index.tsx             # Shell (session 2)
│       ├── Agents/index.tsx              # Shell (session 2)
│       ├── Tasks/index.tsx               # Shell (session 2)
│       ├── Shadow/index.tsx              # Shell (session 3)
│       └── Preferences/index.tsx         # Shell (session 3)
```

## Backend Files

```
src/donna/api/routes/
├── admin_dashboard.py      # 4 dashboard KPI endpoints
├── admin_logs.py           # Log query + trace endpoints
├── admin_invocations.py    # Invocation log browse/detail
├── admin_tasks.py          # Extended task list/detail
└── admin_config.py         # Config/prompt file read (write in session 2)
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

## Progress Tracking

### Session 1 — Status: COMPLETE
- [x] Backend admin API routes (5 files)
- [x] Register routes in FastAPI app
- [x] Frontend project scaffold (React + Vite + Ant Design dark theme)
- [x] API client layer (Axios + typed fetchers)
- [x] Dashboard page (4 KPI cards: Parse Accuracy, Agent Performance, Task Throughput, Cost Analytics)
- [x] Log Viewer (event type tree filter, level/service filters, paginated table, correlation trace drawer)
- [x] Page shells (6 placeholders with feature descriptions for sessions 2-3)
- [x] Docker files (Dockerfile, nginx.conf, donna-ui.yml compose)

### Session 2 — Status: NOT STARTED
- [ ] Config editor pages (structured YAML editing)
- [ ] Prompt editor with live preview
- [ ] Agent detail views
- [ ] Task browser with linked entities
- [ ] PUT endpoints for config/prompt editing
- [ ] Hot reload support for configs

### Session 3 — Status: NOT STARTED
- [ ] Shadow scoring comparison view
- [ ] Preference rules manager
- [ ] UX polish (keyboard shortcuts, saved filters, CSV export)
- [ ] Anomaly notifications
