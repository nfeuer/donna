# Donna — AI Personal Assistant

Donna is an AI-powered personal assistant that actively manages tasks, schedules, reminders, and delegates work to autonomous sub-agents. Named after Donna Paulsen from *Suits* — sharp, confident, efficient, and always one step ahead.

## The Problem

You forget to capture tasks, rarely check task lists, and don't schedule time to do work. Donna fixes that by being proactive: pursuing you with reminders, rescheduling dynamically, preparing for upcoming work, and eventually doing the work for you.

## Architecture

Hub-and-spoke architecture: a central orchestrator manages task routing, scheduling, and agent coordination. All services run as Docker containers on a homelab Linux server.

- **Cloud LLM:** Claude API (Sonnet) for reasoning, parsing, and agent work
- **Local LLM:** Ollama (deferred until RTX 3090 acquired) for classification and routing
- **Database:** SQLite on NVMe (WAL mode) + Supabase Postgres replica
- **Interaction:** Discord bot (primary), Twilio SMS/voice, Gmail
- **Observability:** Grafana + Loki (self-hosted)

See `docs/architecture.md` for the full component map.

## Quick Start

```bash
# Clone and set up
git clone <repo-url> donna
cd donna
cp docker/.env.example docker/.env
# Edit docker/.env with your API keys and config

# Install Python dependencies
pip install -e ".[dev]"

# Run database migrations
alembic upgrade head

# Start in dev mode
donna run --dev

# Or start with Docker
docker compose -f docker/donna-core.yml up --build
```

## Project Structure

```
donna/
├── CLAUDE.md           ← Context anchor for Claude Code sessions
├── docs/               ← Domain specs (split from master spec)
├── slices/             ← Build slice briefs with acceptance criteria
├── config/             ← YAML config (models, task types, state machine)
├── src/donna/          ← Application source code
├── prompts/            ← Externalized LLM prompt templates
├── schemas/            ← JSON schemas for structured LLM output
├── fixtures/           ← Evaluation test fixtures (version-controlled)
├── docker/             ← Compose files and Dockerfiles
├── alembic/            ← Database migration scripts
└── tests/              ← pytest unit + integration tests
```

## Build Phases

| Phase | Goal | Timeline |
|-------|------|----------|
| **1: Foundation** | Task capture, scheduling, reminders, observability | Weeks 1–4 |
| **2: Intelligence** | Multi-channel, prep work, priority escalation, corrections | Weeks 5–7 |
| **3: Agents & Local LLM** | Sub-agents, local model, preference learning | Weeks 8–11 |
| **4: UI & Multi-User** | Flutter app, second user, optimization | Weeks 12+ |

See `slices/` for the Phase 1 implementation plan (10 ordered slices).

## Development

```bash
# Run tests
pytest tests/unit/

# Run with human-readable logs
donna run --dev --log-level DEBUG

# Run evaluation harness (Phase 3+)
donna eval --task-type task_parse --model ollama/llama3.1:8b-q4
```

## Budget

$100/month hard cap on Claude API. $20/day pause threshold for autonomous agent work. Every API call tracked in `invocation_log` table. See `docs/model-layer.md`.

## Documentation

| Doc | Covers |
|-----|--------|
| `docs/architecture.md` | Components, Docker, GPU, storage, concurrency |
| `docs/task-system.md` | Task schema, state machine, dedup, task types |
| `docs/scheduling.md` | Calendar, sync, conflicts, time blocks |
| `docs/model-layer.md` | Model interface, routing, evaluation harness |
| `docs/agents.md` | Agent hierarchy, safety constraints, tool progression |
| `docs/integrations.md` | Hybrid MCP/API strategy, integration matrix |
| `docs/observability.md` | Logging, dashboards, alerting |
| `docs/notifications.md` | Channels, escalation, conversation context |
| `docs/preferences.md` | Correction logging, rule extraction, transparency |
| `docs/resilience.md` | Retries, circuit breaker, backup, security |

## TODO

### Phase 1 — Foundation (complete)

- [x] Wire `donna health` CLI command → `src/donna/resilience/health_check.py`
- [x] Wire `donna backup` CLI command → `src/donna/resilience/backup.py`
- [x] Implement evaluation harness body in `donna eval` → load fixtures from `fixtures/`, run model, compare output (`src/donna/cli.py`)

### Phase 2 — Intelligence (complete)

- [x] Preference rule extraction — extract learned rules from `correction_log` table (`src/donna/preferences/rule_extractor.py`)
- [x] Rule application — apply learned preferences to future task parsing (`src/donna/preferences/rule_applier.py`)
- [x] Weekly planning session — agent assembles and proposes the week's schedule (`src/donna/scheduling/weekly_planner.py`)
- [x] Daily priority recalculation — dynamic priority escalation based on deadlines and workload (`src/donna/scheduling/priority_engine.py`, `src/donna/scheduling/priority_recalculator.py`)
- [x] Dependency chain scheduling — schedule tasks that block other tasks in order (`src/donna/scheduling/dependency_resolver.py`)
- [x] Task prep work agent — execute `prep_work_instructions` before scheduled task start (`src/donna/agents/prep_agent.py`)
- [x] Task decomposition service — break large tasks into subtasks (`src/donna/agents/decomposition.py`)

### Phase 3 — Agents & Local LLM

- [ ] Agent framework — implement agent hierarchy, tool progression, and safety constraints (`src/donna/agents/`)
- [ ] Ollama provider — wire local LLM provider; config entries exist in `config/donna_models.yaml` (deferred until RTX 3090)
- [ ] Tier 4 escalation — TTS phone call via Twilio Voice (tiers 1–3 complete)

### Phase 4 — UI & Multi-User

- [ ] Multi-user orchestration — routing and auth (`user_id` exists on all DB tables)
- [ ] Flutter UI — web + Android app
- [ ] Firebase hosting setup
