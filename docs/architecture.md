# Architecture

> Split from Donna Project Spec v3.0 — Sections 3.1, 3.3, 3.4, 3.5

## Hub-and-Spoke Overview

Central orchestrator manages all task routing, scheduling, and agent coordination. The always-on Linux server is the backbone, running the orchestrator, integration layer, and background agent workers. All services deployed as Docker containers.

## Component Map

| Component | Location | Purpose |
|-----------|----------|---------|
| Orchestrator Service | Linux Server (Docker) | Central brain. Task queue, scheduling engine, agent dispatch, cost monitoring. Runs 24/7. |
| Claude API | Cloud (Anthropic) | Primary LLM for all reasoning. Sole provider until local LLM hardware available. |
| Local LLM (Ollama) | Linux Server (Docker) — RTX 3090 | Task classification, priority inference, routing, simple NLU. Dedicated GPU (24GB VRAM). |
| Integration Layer | Linux Server (Docker) | Internal Python API wrapping all external services. Centralized auth, rate limiting, audit logging. |
| FastMCP Server | Linux Server (Docker) | Dynamic tools for agents via MCP Streamable HTTP. Python (FastMCP 3.x). CodeMode enabled. |
| Task Database | SQLite on NVMe | Primary task storage. Sub-ms reads. WAL mode. |
| Logging Database | SQLite on NVMe (dedicated) | Structured app logs and audit trails. Separate from task DB. |
| Sync Replica | Supabase (Postgres) | Cloud replica for cross-device access. Free tier with keep-alive. |
| Observability Dashboard | Grafana + Loki (Docker) | Real-time log search, filtering, metrics, alerting. Phase 1 deliverable. |
| Notification Service | Linux Server (Docker) | Outbound: email, SMS, phone (TTS), push, Discord. |
| Agent Worker Pool | Linux Server (Docker) | Sandboxed agent execution. Each agent type isolated with defined tool access. |
| Web/Mobile App | Firebase Hosting + Flutter | Dashboard UI and chat interface. Phase 4. See [App Architecture](#app-architecture-phase-4) below. |

## Data Flow

1. All inputs (SMS, Discord, email forwarding) → normalized into task schema by **Input Parser**.
2. Parsing routes through the `ModelRouter`: primary provider (Claude API or local Ollama LLM per config) with optional fallback and shadow mode. See `docs/model-layer.md`.
3. Orchestrator evaluates each task → scheduling engine → routing decision.
4. Agent outputs → stored → reviewed → surfaced via notification service.

## Docker Compose Structure

Multi-file pattern. Each Donna service gets its own compose file, attaching to the shared homelab network.

```
docker/
├── .env.example      ← copy to .env (gitignored)
├── core.yml          ← shared homelab network
├── immich.yml        ← Immich stack (GTX 1080)
├── donna-core.yml    ← Orchestrator, integration layer, notification service
├── donna-monitoring.yml ← Grafana, Loki, Promtail
├── donna-ollama.yml  ← Ollama + local LLM (RTX 3090, added post-GPU)
└── donna-app.yml     ← FastAPI backend (Flutter app connects here)
```

## GPU Isolation

Assignment via environment variables in `docker/.env`:

```env
IMMICH_ML_GPU_ID=0    # GTX 1080 — dedicated to Immich/media
DONNA_OLLAMA_GPU_ID=1 # RTX 3090 — dedicated to Donna LLM
```

No VRAM contention. No GPU sharing between workloads.

## NVMe Storage Layout (1TB Dedicated)

```
/donna/
├── db/
│   ├── donna_tasks.db     ← Primary task SQLite database
│   ├── donna_logs.db      ← Dedicated logging SQLite database
│   └── donna_eval.db      ← Evaluation harness results
├── workspace/             ← Agent sandboxed working directory
├── backups/
│   ├── daily/             ← 7-day retention
│   ├── weekly/            ← 4-week retention
│   ├── monthly/           ← 3-month retention
│   └── offsite/           ← Staging for cloud backup sync
├── logs/
│   └── archive/           ← Compressed historical log exports
├── config/
│   ├── donna_models.yaml
│   ├── task_types.yaml
│   ├── task_states.yaml
│   └── preferences.yaml
├── prompts/               ← Externalized prompt templates
├── fixtures/              ← Evaluation test fixtures (version-controlled)
└── models/                ← Ollama local LLM model cache
```

## Concurrency Model

### Phase 1–2: Single-Threaded Asyncio Event Loop

- Single Python process, asyncio event loop
- All I/O is async (Discord bot, API calls, SQLite, calendar)
- SQLite serialized through single `aiosqlite` connection (WAL mode)
- Calendar writes serialized through async queue (prevents double-booking)
- Task state transitions are atomic: read → validate → write → side effects in single async function with SQLite transaction

### Task Queue with Worker Pool

- `asyncio.Queue` or lightweight broker (arq/Redis)
- Orchestrator dispatches; workers pull and execute independently
- Workers access shared state through orchestrator's internal API only
- Each agent worker is a separate Docker container/process with its own tool access scope

## App Architecture (Phase 4)

The Flutter Web + Android app uses a hybrid approach: Firebase for hosting and authentication, self-hosted FastAPI for all data access. This keeps Firebase's role minimal (CDN + auth) and avoids introducing a second data layer that would compete with the SQLite → Supabase pipeline.

### Responsibilities

| Layer | Technology | Role |
|-------|-----------|------|
| Static hosting | Firebase Hosting | Serves compiled Flutter web app. CDN for fast global delivery. Free tier. |
| User authentication | Firebase Auth | Login, session management, JWT tokens. Handles OAuth flows. Free tier for single-user. |
| Data API | FastAPI (self-hosted, `donna-app.yml`) | REST API between Flutter app and orchestrator. All task, calendar, agent, and cost data flows through here. **Implemented — `src/donna/api/`.** |
| Data storage | SQLite (primary) → Supabase (replica) | Flutter app reads from Supabase for cross-device access. Writes go through FastAPI → orchestrator → SQLite → Supabase sync. |
| Push notifications | FCM (Firebase Cloud Messaging) | Android push notifications. Free tier. |

### What Firebase Does NOT Do

- **No Firestore.** Task data lives in SQLite → Supabase. Adding Firestore would create a third data store and require a Supabase ↔ Firestore sync strategy — unnecessary complexity.
- **No Firebase Functions.** All server-side logic runs in the self-hosted orchestrator and FastAPI backend.
- **No Firebase Realtime Database.** Supabase Postgres handles cross-device data access.

### Auth Flow

1. Flutter app authenticates user via Firebase Auth (email/password or Google OAuth).
2. Firebase issues a JWT token.
3. Flutter sends JWT with every request to FastAPI backend.
4. FastAPI validates the JWT against Firebase's public keys (no Firebase SDK needed server-side — just JWT verification via `PyJWT` + Google JWKS endpoint).
5. FastAPI maps the Firebase UID to Donna's `user_id` via `DONNA_USER_MAP` env var for all downstream operations.

**Implementation:** `src/donna/api/auth.py` — `get_current_user_id` FastAPI dependency. JWKS cached in-process for 1 hour. Dev bypass via `DONNA_AUTH_DISABLED=true`.

### REST API Endpoints (implemented)

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Unauthenticated service health check |
| `GET /tasks` | List tasks (paginated, filterable by status/domain) |
| `POST /tasks` | Create task |
| `GET /tasks/{id}` | Get single task |
| `PATCH /tasks/{id}` | Update task fields |
| `DELETE /tasks/{id}` | Cancel task |
| `GET /schedule` | Upcoming scheduled tasks (configurable window, default 7 days) |
| `GET /schedule/week` | 7-day schedule (alias) |
| `GET /agents/activity` | Recent LLM invocations from `invocation_log` |
| `GET /agents/cost` | Daily + monthly cost totals vs budget |

All endpoints (except `/health`) require `Authorization: Bearer <firebase_jwt>` and return user-scoped data only.

Start the API: `uvicorn donna.api:app --host 0.0.0.0 --port 8200`

### Multi-User Data Model

All core tables have had `user_id` since Phase 1. The `calendar_mirror` table received `user_id` in Phase 4 via migration `add_calendar_mirror_user_id` (backfilled with `"nick"` for existing rows). The data model is now fully multi-user ready.

### Flutter App

The Flutter Web + Android app (`donna-app` — separate repository) connects to this FastAPI backend. See `slices/slice_11_flutter_ui.md` for the full spec, screen breakdown, and acceptance criteria.

## Schema Migration

- SQLAlchemy models define all tables
- Alembic manages schema evolution for both task DB and logging DB
- On startup: `alembic upgrade head` applies pending migrations
- Every schema change → new migration file with `upgrade()` and `downgrade()`
- Never modify existing migration files
- Pre-migration backup created automatically before applying
