# Architecture

> Split from Donna Project Spec v3.0 — Sections 3.1, 3.3, 3.4, 3.5

## Hub-and-Spoke Overview

Central orchestrator manages all task routing, scheduling, and agent coordination. The always-on Linux server is the backbone, running the orchestrator, integration layer, and background agent workers. All services deployed as Docker containers.

## Component Map

| Component | Location | Purpose |
|-----------|----------|---------|
| Orchestrator Service | Linux Server (Docker) | Central brain. Task queue, scheduling engine, agent dispatch, cost monitoring. Runs 24/7. |
| Claude API | Cloud (Anthropic) | Primary LLM for all reasoning. Sole provider until local LLM hardware available. |
| Local LLM (Ollama) | Linux Server (Docker) — RTX 3090 | **DEFERRED.** Task classification, priority inference, routing, simple NLU. Dedicated GPU. |
| Integration Layer | Linux Server (Docker) | Internal Python API wrapping all external services. Centralized auth, rate limiting, audit logging. |
| FastMCP Server | Linux Server (Docker) | Dynamic tools for agents via MCP Streamable HTTP. Python (FastMCP 3.x). CodeMode enabled. |
| Task Database | SQLite on NVMe | Primary task storage. Sub-ms reads. WAL mode. |
| Logging Database | SQLite on NVMe (dedicated) | Structured app logs and audit trails. Separate from task DB. |
| Sync Replica | Supabase (Postgres) | Cloud replica for cross-device access. Free tier with keep-alive. |
| Observability Dashboard | Grafana + Loki (Docker) | Real-time log search, filtering, metrics, alerting. Phase 1 deliverable. |
| Notification Service | Linux Server (Docker) | Outbound: email, SMS, phone (TTS), push, Discord. |
| Agent Worker Pool | Linux Server (Docker) | Sandboxed agent execution. Each agent type isolated with defined tool access. |
| Web/Mobile App | Firebase Hosting + Flutter | Dashboard UI and chat interface. Phase 4. |

## Data Flow

1. All inputs (SMS, Discord, email forwarding) → normalized into task schema by **Input Parser**.
2. Phase 1–2: parsing on Claude API. Phase 3+: high-frequency parsing shifts to local LLM with Claude fallback.
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
└── models/                ← Ollama model cache (Phase 3+)
```

## Concurrency Model

### Phase 1–2: Single-Threaded Asyncio Event Loop

- Single Python process, asyncio event loop
- All I/O is async (Discord bot, API calls, SQLite, calendar)
- SQLite serialized through single `aiosqlite` connection (WAL mode)
- Calendar writes serialized through async queue (prevents double-booking)
- Task state transitions are atomic: read → validate → write → side effects in single async function with SQLite transaction

### Phase 3+: Task Queue with Worker Pool

- `asyncio.Queue` or lightweight broker (arq/Redis)
- Orchestrator dispatches; workers pull and execute independently
- Workers access shared state through orchestrator's internal API only
- Each agent worker is a separate Docker container/process with its own tool access scope

## Schema Migration

- SQLAlchemy models define all tables
- Alembic manages schema evolution for both task DB and logging DB
- On startup: `alembic upgrade head` applies pending migrations
- Every schema change → new migration file with `upgrade()` and `downgrade()`
- Never modify existing migration files
- Pre-migration backup created automatically before applying
