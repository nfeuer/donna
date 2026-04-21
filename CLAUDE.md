# Donna — AI Personal Assistant

## What This Is
Donna is an AI personal assistant that actively manages tasks, schedules, reminders, and delegates work to sub-agents. Named after Donna Paulsen from Suits — sharp, confident, efficient, never sycophantic. Built for a single user (Nick) with multi-user data model from day one.

## Core Problem
The user forgets to capture tasks, rarely checks task lists, and doesn't schedule time to do work. Donna solves this by being proactive — pursuing the user, demanding updates, rescheduling dynamically, and doing prep work autonomously.

## Tech Stack
- **Language:** Python 3.12+ / asyncio
- **Cloud LLM:** Claude API (claude-sonnet-4-20250514) — sole provider until local LLM hardware available
- **Local LLM:** Ollama on RTX 3090 (`qwen2.5:32b-instruct-q6_K`)
- **Database:** SQLite on NVMe (WAL mode) — `donna_tasks.db` + `donna_logs.db`
- **Cloud Replica:** Supabase Postgres (free tier, async write-through sync)
- **Schema Migration:** Alembic (SQLAlchemy models)
- **Interaction:** Discord bot (discord.py) as primary channel; Twilio SMS/voice; Gmail API
- **Observability:** Grafana + Loki + Promtail (self-hosted Docker)
- **Deployment:** Docker Compose (multi-file homelab pattern)
- **Future UI:** Flutter (Web + Android), Firebase Hosting

## Key Design Principles — Follow These Always
1. **Config over code.** Model routing, task types, state transitions, prompt templates, and preferences are YAML/JSON config. Never hardcode these in application logic.
2. **Safety first, dial back later.** Agents start with minimal autonomy. Email is draft-only. Code goes to feature branches only. Constraints are relaxed explicitly via config, never assumed.
3. **Structured logging on every model call.** Every LLM invocation logs: task_type, model, latency, tokens, cost, output. No exceptions.
4. **Internal API over MCP for orchestrator calls.** The orchestrator calls integrations via direct Python modules. MCP is only for LLM-facing dynamic tool discovery.
5. **Model abstraction.** All LLM calls go through `complete(prompt, schema, model_alias)`. Never call a provider directly.
6. **Tool validation layer.** Models propose tool calls; the orchestrator validates and executes. Models never call tools directly.

## Directory Layout
- `spec_v3.md` — **Canonical design document.** All architectural decisions trace back to this file. Cite `§` sections when introducing or changing design.
- `IMPLEMENTATION_GUIDE.md` — Implementation companion to `spec_v3.md`.
- `docs/` — Browsable documentation site (MkDocs). Narrative under `docs/architecture/`, `docs/domain/`, `docs/workflows/`, `docs/development/`, `docs/operations/`; auto-generated API reference under `docs/reference/`; canonical specs embedded under `docs/reference-specs/`. **Read the relevant `docs/domain/*.md` before making architectural decisions, and consult `spec_v3.md` for authority.**
- `config/` — YAML config files (models, task types, state machine, preferences)
- `src/donna/` — Application source code
- `prompts/` — Externalized prompt templates (Jinja2 or plain markdown)
- `schemas/` — JSON schemas for structured LLM output
- `fixtures/` — Evaluation test fixtures (version-controlled)
- `slices/` — Phase 1 build slice briefs with acceptance criteria
- `docker/` — Compose files and env template
- `tests/` — pytest unit + integration tests
- `mkdocs.yml`, `scripts/gen_ref_pages.py` — Docs site config and auto-generator

## Budget
- $100/month hard cap on Claude API
- $20/day pause threshold — all autonomous agent work stops
- Every API call tracked in `invocation_log` table

## Before You Start a Task
1. Read this file.
2. For any design decision, consult `spec_v3.md` (and cite the relevant `§` in your PR description).
3. Identify which `docs/domain/*.md` files are relevant to the task.
4. Read the slice brief in `slices/` if working on a specific slice.
5. Check `config/` for any config structures your code should read from.
6. Run `pytest` before and after changes.

## Conventions
- Async everywhere — use `async def` and `await` for all I/O.
- Type hints on all function signatures.
- Structured logging via `structlog` — never use `print()`.
- SQLite access via `aiosqlite` — single connection, WAL mode.
- All task state transitions go through the state machine (loaded from `config/task_states.yaml`).
- Schema changes require an Alembic migration — never modify tables manually.

## Documentation
- Narrative docs are hand-written markdown under `docs/`.
- API reference, config pages, and schema pages are **auto-generated** on every `mkdocs build` by `scripts/gen_ref_pages.py` — never commit files under `docs/reference/`, `docs/config/`, or `docs/schemas/`.
- Docstring style: **Google** (rendered by `mkdocstrings`). New modules must have a docstring; new public functions/classes need at least `Args` / `Returns` / `Raises`.
- Local preview: `pip install -e ".[docs]" && mkdocs serve`.
- Deploy: handled by `.github/workflows/docs.yml` on push to `main`.
- **Any design work** — in PR descriptions, commit messages, or doc pages — must reference `spec_v3.md` with the relevant `§` section.
