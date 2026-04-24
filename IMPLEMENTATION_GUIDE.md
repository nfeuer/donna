# Donna — Implementation Guide

## Overview

Donna is an AI personal assistant that actively manages tasks, schedules, reminders, and delegates work to sub-agents. Named after Donna Paulsen from *Suits* — sharp, confident, efficient, never sycophantic.

This document is the **procedural companion to `spec_v3.md`**. The spec is canonical for design intent; this guide is how you stand the system up phase by phase. Every architectural claim here traces back to a `§` section in the spec and a file in the repo — if you find drift, update both.

**Architecture:** Hub-and-spoke. A central orchestrator routes work through a model abstraction layer (`complete(prompt, schema, model_alias)`), validates tool calls proposed by models, and delegates long-running work to an automation subsystem (`spec_v3.md §25`).

**Primary interface:** Discord bot. Secondary: Twilio SMS/voice, Gmail (draft-only), REST API + React admin UI behind a Caddy reverse proxy.

**Data:** SQLite on NVMe (WAL mode, `donna_tasks.db`), with async Supabase Postgres write-through replica. Service logs stream to Loki (`spec_v3.md §14.3.1`). All LLM calls tracked in `invocation_log` for budget control (`§4.3`).

**Related docs:**

- [`spec_v3.md`](spec_v3.md) — canonical design (v3.1 synced to production in commit `47a1a5f`).
- [`SETUP.md`](SETUP.md) — hands-on install walkthrough with third-party signup steps.
- [`INSTALL_DAY.md`](INSTALL_DAY.md) — hour-by-hour playbook for hardware install day.
- [`CLAUDE.md`](CLAUDE.md) — contributor conventions and budget rules.
- [`docs/architecture/overview.md`](docs/architecture/overview.md) — narrative architecture tour.

---

## Phase 0: Prerequisites

### 0.1 Hardware

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Linux 64-bit | Ubuntu 22.04+ |
| RAM | 4 GB | 16 GB+ |
| Storage | 10 GB free | NVMe preferred |
| GPU (Phase 1–2) | None | — |
| GPU (Phase 3+) | RTX 3090 (24 GB VRAM) | RTX 3090 dedicated to Ollama |

Two-GPU homelab layout (optional, `spec_v3.md §3.5.2`): a secondary card (e.g. GTX 1080) runs Immich ML (`IMMICH_ML_GPU_ID`) while the RTX 3090 is dedicated to Donna's Ollama (`DONNA_OLLAMA_GPU_ID`).

### 0.2 Software

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3.12-dev
sudo apt install -y docker.io docker-compose-v2 git curl
sudo usermod -aG docker $USER && newgrp docker
```

> **Windows users:** install [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and run everything inside Ubuntu 22.04.

### 0.3 External Accounts

The table lists what each service unlocks and which phase first needs it. Full signup walkthroughs live in [`SETUP.md`](SETUP.md) — do not duplicate them here.

| Service | Unlocks | First needed | Cost |
|---------|---------|--------------|------|
| Anthropic | Cloud LLM (Claude) | Phase 1 (required) | Pay-per-use |
| Discord | Primary chat + proactive channels | Phase 1 (required) | Free |
| Google Cloud | Calendar + Gmail APIs | Phase 2 | Free |
| Twilio | SMS/voice escalation | Phase 2 | Pay-per-use |
| Supabase | Cloud Postgres replica | Phase 2 | Free tier |
| Immich | Phase 4 auth allowlist source-of-truth | Phase 4 | Self-hosted |

Phase 4 auth is gated by an existing Immich instance — Donna does not provision one. You need its internal URL and an **admin** API key before starting Phase 4 (see §4.1).

---

## Phase 1: Core System (Required)

Phase 1 stands up the orchestrator, SQLite, Claude routing, Discord, and the monitoring stack. When this phase is green you have a working Donna that responds in Discord and emits structured logs.

### 1.1 Clone the Repository

```bash
git clone <repo-url> donna
cd donna
```

### 1.2 Create Storage Directories

Paths match `DONNA_DATA_PATH` defaults (see [`docker/.env.example`](docker/.env.example)).

```bash
sudo mkdir -p /donna/{db,workspace,backups/{daily,weekly,monthly,offsite},logs/archive,config,prompts,fixtures,models}
sudo chown -R $USER:$USER /donna
```

```
/donna/
├── db/                  # donna_tasks.db (WAL mode); donna_logs.db if DONNA_LOGS_DB_PATH set
├── workspace/           # Agent scratch space
├── backups/{daily,weekly,monthly,offsite}/
├── logs/archive/
├── config/              # Runtime config, OAuth tokens (google_credentials.json, google_token.json)
├── prompts/             # Externalized prompt templates
├── fixtures/            # Evaluation test fixtures
└── models/              # Local model cache (Phase 3)
```

Only `donna_tasks.db` is created in normal operation. `DONNA_LOGS_DB_PATH` is referenced by `donna health` for self-diagnostics; general service logs flow to Loki (`spec_v3.md §14.3.1`).

### 1.3 Python Environment

**Option A — uv (recommended):**

```bash
pip install uv
uv venv .venv --python 3.12
source .venv/bin/activate
uv sync --extra dev
```

**Option B — pip:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
donna --help   # lists: run, eval, health, backup, setup, test-notification
```

### 1.4 Environment Configuration

```bash
cp docker/.env.example docker/.env
```

Minimum required for Phase 1:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key (`sk-ant-...`) |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_GUILD_ID` | Your Discord server ID |
| `DISCORD_TASKS_CHANNEL_ID` | Channel for task messages |
| `DISCORD_DIGEST_CHANNEL_ID` | Channel for daily digests |
| `DISCORD_AGENTS_CHANNEL_ID` | Channel for agent status |
| `DISCORD_DEBUG_CHANNEL_ID` | Channel for debug output |
| `DONNA_MONTHLY_BUDGET_USD` | Monthly cost cap (default: 100.00) |
| `DONNA_DAILY_PAUSE_THRESHOLD_USD` | Daily pause threshold (default: 20.00) |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin password |

Storage paths default to `/donna/…` and don't need editing if you followed §1.2. The full, phase-organised list lives in Appendix E.

> **Prefer the wizard?** `donna setup --phase 1` walks you through the same variables interactively and writes `docker/.env` for you.

### 1.5 Database Setup

```bash
alembic upgrade head
```

This applies all 34 migrations — Phase 1 through Phase 5 tables (skills, automations, auth, chat, capabilities, calendar mirror, SMS escalation, nudge events, and more). See Appendix D for the breakdown.

Schema changes go through Alembic (`spec_v3.md §3.8`) — never edit tables by hand.

Verify:

```bash
alembic current    # latest revision hash
alembic history    # full migration chain
```

### 1.6 Review Config Files

These ship with sensible defaults — no edits required for Phase 1, but skim them so you know where to tune later. Sixteen config files live under `config/`; the complete list with one-line purposes is in Appendix B. Phase 1 only reads a handful:

| File | Purpose |
|------|---------|
| `config/donna_models.yaml` | Model routing, cost tracking, Ollama settings, task-type → model map |
| `config/task_types.yaml` | Task type registry: models, prompts, schemas, tools, shadow models |
| `config/task_states.yaml` | State machine: states, transitions, triggers, side effects |
| `config/llm_gateway.yaml` | Queue scheduling, rate limits, priority map, Ollama health checks |
| `config/agents.yaml` | Agent roster (PM, Scheduler, Research, Challenger, etc.) and autonomy levels |

### 1.7 Run Locally (Smoke Test)

Compose auto-loads `docker/.env` when invoked from the `docker/` directory, but for the in-shell smoke test you need to source it yourself. Use `set -a` so quoted and spaced values survive:

```bash
set -a; source docker/.env; set +a
donna run --dev --log-level DEBUG
```

Verify:

```bash
curl http://localhost:8100/health
# Expected: 200 OK
```

Send a message in your tasks channel — Donna should acknowledge it.

Override the port with `--port 9000` or `DONNA_PORT=9000`; see `donna run --help` for every flag.

### 1.8 Docker Deployment

All services attach to an external `homelab` Docker network (`spec_v3.md §3.5.1`). Create it once:

```bash
docker network create homelab
```

Bring up the core orchestrator:

```bash
docker compose -f docker/donna-core.yml --env-file docker/.env up --build -d
```

Check:

```bash
docker ps                              # donna-orchestrator should show Up (healthy)
docker logs donna-orchestrator         # structured JSON logs
curl http://localhost:8100/health     # 200 OK
```

### 1.9 Monitoring Stack

Recommended in Phase 1 so every subsequent phase benefits from dashboards:

```bash
docker compose -f docker/donna-monitoring.yml --env-file docker/.env up -d
```

| Service | Image | Port |
|---------|-------|------|
| `donna-loki` | `grafana/loki:2.9.0` | 3100 |
| `donna-promtail` | `grafana/promtail:2.9.0` | (internal) |
| `donna-grafana` | `grafana/grafana:10.2.0` | 3000 |

Visit `http://localhost:3000` (user `admin`, password from `GRAFANA_ADMIN_PASSWORD`). Four dashboards auto-provision: **Cost**, **Health**, **Pipeline**, **Errors** (provisioning files live under [`docker/grafana/`](docker/grafana/)).

### 1.10 Run Tests

```bash
pytest tests/unit/
pytest tests/integration/
```

### Gate Check: Phase 1

- [ ] `donna --help` lists all six subcommands
- [ ] `alembic current` shows the latest revision
- [ ] `curl localhost:8100/health` returns 200
- [ ] Unit tests pass
- [ ] `docker ps` shows `donna-orchestrator` (and monitoring) as healthy
- [ ] Grafana reachable at `:3000` with the four dashboards visible
- [ ] Discord bot responds in the tasks channel

---

## Phase 2: External Integrations (Recommended)

Each integration is independently optional — the orchestrator degrades gracefully if its env vars are unset. Signup steps live in [`SETUP.md`](SETUP.md); this section covers only the wiring.

### 2.1 Google Calendar

```bash
# Place the OAuth client JSON from Google Cloud:
cp /path/to/credentials.json /donna/config/google_credentials.json
```

In `docker/.env`:

```
GOOGLE_CREDENTIALS_PATH=/donna/config/google_credentials.json
GOOGLE_CALENDAR_PERSONAL_ID=primary
GOOGLE_CALENDAR_WORK_ID=<calendar-id>
GOOGLE_CALENDAR_FAMILY_ID=<calendar-id>
```

First run opens a browser for consent; the cached token lands at `/donna/config/google_token.json`. Behaviour (polling, time-window rules, blackout/quiet/work/personal/weekend) is tuned in [`config/calendar.yaml`](config/calendar.yaml).

### 2.2 Gmail

Uses the same Google credentials. The Gmail API must be enabled in the Google Cloud project. Access is **read + draft-only by default** (per the safety-first principle in `CLAUDE.md`). Configure digests, forwarding alias, and draft caps in [`config/email.yaml`](config/email.yaml).

### 2.3 Twilio SMS/Voice

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX
DONNA_USER_PHONE=+1XXXXXXXXXX
```

Point the Twilio inbound-message webhook at `http://<your-server>:8100/webhooks/sms`.

Escalation policy lives in [`config/sms.yaml`](config/sms.yaml): rate limit 10/day, escalation ladder (30 min SMS → 60 min email → 120 min phone), blackout hours.

### 2.4 Supabase Cloud Replica

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

Schema is created automatically by the async write-through sync on first run. Free-tier projects auto-pause after inactivity — install the keepalive cron:

```bash
crontab -e
# 0 12 * * 1 /path/to/donna/scripts/supabase_keepalive.sh
```

### 2.5 Obsidian Vault + Memory Store (slices 12 + 13)

Donna-owned markdown vault (slice 12) plus a sqlite-vec-backed semantic index over it (slice 13). Both are optional — the orchestrator runs fine without them:

- If `config/memory.yaml` is absent or the vault root is unreachable, the vault skill tools simply aren't registered.
- If `sqlite-vec` fails to load (wheel missing on the host platform), `Database.vec_available` stays `False`, the memory store isn't built, and `memory_search` stays off the tool registry — every other subsystem keeps booting.

See [`docs/domain/memory-vault.md`](docs/domain/memory-vault.md) for the narrative and [`docs/reference-specs/memory-vault-spec.md`](docs/reference-specs/memory-vault-spec.md) for the design spec.

**Create the vault root on the host:**

```bash
sudo mkdir -p /donna/vault
sudo chown -R $USER:$USER /donna/vault
```

**Generate the WebDAV basic-auth hash** (don't leave plaintext in the shell history):

```bash
docker run --rm caddy:2 caddy hash-password -p '<choose a strong password>'
# → $2a$14$…
```

**`docker/.env`:**

```
DONNA_VAULT_PATH=/donna/vault
CADDY_VAULT_USER=donna
CADDY_VAULT_PASSWORD_HASH=$2a$14$…   # paste the hash from above
```

**`config/memory.yaml`** ships with sensible defaults — review `vault.root`, `vault.git_author_email`, `safety.path_allowlist`, and (new in slice 13) `embedding.provider` / `retrieval.default_k` / `sources.vault.ignore_globs` if you want a different layout. MiniLM-L6-v2 is the default provider; swapping is a config-only change plus a factory branch in `donna.memory.embeddings.build_embedding_provider`.

**Bring up the WebDAV service:**

```bash
cp docker/caddy/vault.Caddyfile.example docker/caddy/vault.Caddyfile
docker compose -f docker/donna-vault.yml up -d
curl -u "$CADDY_VAULT_USER:<plaintext>" -X PROPFIND http://localhost:8500/
# Expect: 207 Multi-Status
```

**Restart the orchestrator** so it picks up the vault mount and the five `vault_*` tools plus `memory_search` register for the `pm`, `scheduler`, `research`, and `challenger` agents.

On first boot with slice 13 installed, the `VaultSource.backfill` task walks the vault root and ingests every `.md` — expect `~N seconds` for N notes the first time (chunking + embedding). Subsequent boots only re-embed files whose mtime advanced past the stored `memory_documents.updated_at`, so the steady-state cost is near-zero.

**Obsidian clients:** operator guide at [`docs/operations/vault-sync.md`](docs/operations/vault-sync.md).

### Gate Check: Phase 2

- [ ] Calendar events appear in the calendar mirror after the first sync
- [ ] A Gmail draft can be created via the `email_triage` task type
- [ ] Twilio inbound message reaches the orchestrator (if configured)
- [ ] Supabase dashboard shows active connections
- [ ] (slice 12) `scripts/dev_tool_call.py --config-dir config vault_write --path "Inbox/smoke.md" --content '# hi'` returns a commit SHA
- [ ] (slice 12) Obsidian desktop connects to the WebDAV endpoint and sees the vault contents

---

## Phase 3: Local LLM (Optional — Requires RTX 3090)

Moves Claude off the critical path for cheap, high-volume task types (parsing, classification) while keeping reasoning on Claude. Shadow first, then cut over (`spec_v3.md §4.4`).

### 3.1 GPU Prerequisites

**NVIDIA driver:**

```bash
nvidia-smi  # check if already installed
sudo apt install -y nvidia-driver-535
sudo reboot
```

**NVIDIA Container Toolkit:**

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify Docker GPU access:

```bash
docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi
```

### 3.2 Start Ollama

Set the GPU index in `docker/.env`:

```
DONNA_OLLAMA_GPU_ID=1       # RTX 3090 dedicated to Donna
IMMICH_ML_GPU_ID=0          # optional secondary card for Immich ML
```

Single-GPU hosts can omit `IMMICH_ML_GPU_ID` and set `DONNA_OLLAMA_GPU_ID=0`.

```bash
docker compose -f docker/donna-ollama.yml --env-file docker/.env up -d
docker exec donna-ollama nvidia-smi  # verify GPU visible inside the container
```

### 3.3 Pull Model

The production model is `qwen2.5:32b-instruct-q6_K` (per `CLAUDE.md` and [`config/donna_models.yaml`](config/donna_models.yaml)). Higher quantization than q4_K_M; larger VRAM footprint; better output quality.

```bash
docker exec donna-ollama ollama pull qwen2.5:32b-instruct-q6_K
docker exec donna-ollama ollama list
```

### 3.4 Smoke Test

```bash
docker exec -it donna-ollama ollama run qwen2.5:32b-instruct-q6_K \
  "Extract the task: 'remind me to call the dentist Thursday'. Reply JSON: {\"task\": \"\", \"due\": \"\"}"
```

Check VRAM:

```bash
docker exec donna-ollama nvidia-smi --query-gpu=memory.used,memory.free --format=csv
```

### 3.5 Evaluation Harness

Gate cutover on `donna eval` (`spec_v3.md §4.5`):

```bash
donna eval --task-type task_parse --model ollama/qwen2.5:32b-instruct-q6_K
donna eval --task-type classify_priority --model ollama/qwen2.5:32b-instruct-q6_K
```

Pass gates:

- **Tier 1** ≥ 90%
- **Tier 2** ≥ 80%
- **Tier 3** ≥ 60%

Restrict to one tier with `--tier 1`.

### 3.6 Shadow Mode (1–2 Weeks)

Edit [`config/donna_models.yaml`](config/donna_models.yaml) — add a `shadow` block so the local model runs in parallel with Claude but outputs aren't used:

```yaml
models:
  parser:
    provider: anthropic
    model: claude-sonnet-4-20250514
    shadow:
      provider: ollama
      model: qwen2.5:32b-instruct-q6_K
```

Restart the orchestrator. Shadow invocations land in `invocation_log` tagged as `shadow` — compare via the Grafana **Cost** and **Pipeline** dashboards.

### 3.7 Hybrid Routing

Once shadow quality is confirmed, promote parser to Ollama while keeping reasoner on Claude:

```yaml
models:
  parser:
    provider: ollama
    model: qwen2.5:32b-instruct-q6_K
    estimated_cost_per_1k_tokens: 0.0001
  reasoner:
    provider: anthropic
    model: claude-sonnet-4-20250514
  fallback:
    provider: anthropic
    model: claude-sonnet-4-20250514
```

Enable 5% spot-check sampling for ongoing quality monitoring. To revert: flip `parser.provider` back to `anthropic` and restart.

### Gate Check: Phase 3

- [ ] `curl localhost:11434/api/tags` lists `qwen2.5:32b-instruct-q6_K`
- [ ] Eval harness passes Tier 1 and Tier 2 gates
- [ ] Shadow entries visible in `invocation_log` with matching task IDs
- [ ] VRAM used matches the q6_K footprint on your GPU

---

## Phase 4: REST API + Management UI + Immich-Gated Auth

Phase 4 brings up the read-only admin API (`donna-api`, port 8200), the React admin panel (`donna-ui`, port 8400), and the Caddy reverse proxy that fronts both. Auth is **Immich-gated** — there is no Firebase, no password database, no self-service signup (`spec_v3.md §28`).

### 4.1 Immich Allowlist + Env Vars

Donna syncs allowed emails every 15 minutes from Immich via the admin API. Immich is the single source of truth for who may log in.

Required in `docker/.env`:

```
IMMICH_ADMIN_API_KEY=<admin-scoped key from Immich>
DONNA_BOOTSTRAP_ADMIN_EMAIL=you@example.com
DONNA_CORS_ORIGINS=https://donna.yourdomain.tld
```

- `IMMICH_ADMIN_API_KEY` — the API refuses to start without it.
- `DONNA_BOOTSTRAP_ADMIN_EMAIL` — the first user to successfully verify this email gets auto-promoted to admin.
- `DONNA_CORS_ORIGINS` — **concrete allowlist** (comma-separated). A wildcard `*` is rejected at startup because auth uses cookies.

Immich URLs, token lifetimes, and proxy settings live in [`config/auth.yaml`](config/auth.yaml):

- IP-trust ladder: 24h / 7d / 30d (default) / 90d
- Magic-link expiry: 15 minutes
- Device-token cookie: 90-day sliding, 365-day absolute, max 10 per user
- Rate limits: 5 request-access/hour/IP, 10 verify/10min/IP
- Trusted proxies: `172.18.0.0/16` (homelab Docker network)

### 4.2 Auth Flow (Summary)

Routes live in [`src/donna/api/routes/auth_flow.py`](src/donna/api/routes/auth_flow.py); implementation modules under `src/donna/api/auth/` (Immich client, IP gate, email allowlist, magic-link tokens, device tokens, trusted proxies).

1. `POST /auth/request-access` — email → magic link (15 min expiry) sent via the configured mailer.
2. `POST /auth/verify` — burns the magic-link token, marks the IP trusted for the default window, issues a device-token cookie.
3. `GET /auth/status` — returns the caller's session state.
4. `POST /auth/logout` — revokes the device token cookie.

Trust rules from `config/auth.yaml` are enforced per request. See `spec_v3.md §28` for the design intent.

### 4.3 Deploy the API

```bash
docker compose -f docker/donna-app.yml --env-file docker/.env up --build -d
```

The API opens the SQLite file read-only (`DONNA_DB_PATH`). Verify:

```bash
curl http://localhost:8200/health   # 200 OK
```

### 4.4 Deploy the UI

```bash
docker compose -f docker/donna-ui.yml --env-file docker/.env up --build -d
```

The UI container builds from the sibling repo at `../donna-ui` and serves static assets on port 8400.

### 4.5 Put Caddy in Front

Caddy is not part of the repo Compose — run it as your own service (the Dockerised-homelab pattern). An example Caddyfile is shipped at [`docker/caddy/donna.Caddyfile.example`](docker/caddy/donna.Caddyfile.example) and terminates TLS, routing:

- `/api/*` → `donna-api:8200`
- `/` → `donna-ui:8400`

Caddy must sit inside the `homelab` Docker network so it can resolve the container DNS names, and its public IP must fall inside the `trusted_proxies` CIDR from §4.1.

### 4.6 First Login

1. Visit the UI origin (e.g. `https://donna.yourdomain.tld`).
2. Enter your email (must exist as a user in Immich).
3. Receive the magic link, click it → your IP is now trusted and a device-token cookie is set.
4. Because your email matches `DONNA_BOOTSTRAP_ADMIN_EMAIL`, you are promoted to admin automatically.

### Gate Check: Phase 4

- [ ] `curl localhost:8200/health` returns 200
- [ ] API start fails loudly if `IMMICH_ADMIN_API_KEY` is unset (by design)
- [ ] Wildcard `DONNA_CORS_ORIGINS=*` causes start-up refusal (by design)
- [ ] Magic-link verification returns a `Set-Cookie` with the device token
- [ ] First verified login matching `DONNA_BOOTSTRAP_ADMIN_EMAIL` is admin
- [ ] UI at `https://donna.yourdomain.tld` loads through Caddy with TLS

---

## Phase 5: Automations

The automations subsystem (`src/donna/automations/`, `spec_v3.md §25`) cron-schedules already-registered skills. A scheduler polls the DB; a dispatcher validates cadence and cost before routing to the execution engine. No GPU, no new external services — Phase 5 builds on top of what Phases 1–4 already registered.

### 5.1 Prerequisites

- A skill is only automatable at **sandbox** lifecycle stage or better (`flagged_for_review` is paused by policy).
- Budget from Phase 1 (`DONNA_DAILY_PAUSE_THRESHOLD_USD`) also gates automation runs.
- The orchestrator must be running — the scheduler is an asyncio task inside it, not a separate container.

### 5.2 Runtime Settings (Source-Level Defaults)

These live in [`src/donna/config.py`](src/donna/config.py) and are read at boot:

| Setting | Default | Meaning |
|---------|---------|---------|
| `automation_poll_interval_seconds` | `15` | How often the scheduler wakes to look for due automations (tuned for responsive "run now"). |
| `automation_failure_pause_threshold` | `5` | Consecutive failures before an automation is paused. |
| `automation_max_cost_per_run_default_usd` | `2.0` | Hard cap on a single automation run. |
| `nightly_run_hour_utc` | `3` | When the nightly skill-evolution job runs. |

### 5.3 Cadence Policy

Minimum intervals by skill lifecycle class, from [`config/automations.yaml`](config/automations.yaml):

| Class | `min_interval_seconds` |
|-------|------------------------|
| `claude_native` | 43200 (12 h) |
| `sandbox` | 43200 (12 h) |
| `shadow_primary` | 3600 (1 h) |
| `trusted` | 900 (15 min) |
| `degraded` | 43200 (12 h) |
| `flagged_for_review` | **paused** |

Automations created via the Discord natural-language path default to `discord_automation_default_min_interval_seconds = 300` (5 min) — tightened from the class default so chat-initiated automations feel responsive.

### 5.4 Creating an Automation

Two paths:

1. **Admin API / UI** — `POST /automations` (see `src/donna/api/routes/automations.py`). The orchestrator scheduler picks the new row up on its next poll.
2. **Discord NL** — ask Donna in chat; the intent dispatcher (`§23.2`) creates the row with the 5 min minimum.

Rows include: user, skill name, cron expression, cadence overrides, active flag, last/next run. Unique on `(user_id, name)`.

### 5.5 Observability

- **Grafana → Pipeline dashboard** — per-automation run counts, latency, failure rate.
- **Grafana → Cost dashboard** — spend per automation vs. the per-run cap.
- `invocation_log` rows for automation-driven LLM calls carry the skill ID (migration `b9d2e4f6a135_add_skill_id_to_invocation_log.py`).

### Gate Check: Phase 5

- [ ] Creating an automation row causes the scheduler to log a "due" event within `automation_poll_interval_seconds`
- [ ] A failing automation is paused after 5 consecutive failures
- [ ] A run exceeding `automation_max_cost_per_run_default_usd` is aborted
- [ ] Discord-created automations persist with `min_interval_seconds = 300`

---

## Appendices

### A. CLI Reference

All flags verified against [`src/donna/cli.py`](src/donna/cli.py).

**`donna run`** — start the orchestrator.

| Flag | Default | Choices |
|------|---------|---------|
| `--config-dir` | `config` | — |
| `--log-level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `--dev` | off | flag — human-readable logs |
| `--port` | `DONNA_PORT` env or `8100` | int |

**`donna eval`** — run the evaluation harness (`spec_v3.md §4.5`).

| Flag | Default | Notes |
|------|---------|-------|
| `--task-type` | — | **required** (e.g. `task_parse`, `classify_priority`) |
| `--model` | — | **required**, `provider/model` form (e.g. `ollama/qwen2.5:32b-instruct-q6_K`) |
| `--fixtures-dir` | `fixtures` | — |
| `--tier` | all tiers | `1`–`4` to restrict |

**`donna health`** — self-diagnostic; reads `DONNA_DB_PATH` (`donna_tasks.db`) and optionally `DONNA_LOGS_DB_PATH` (`donna_logs.db`). No flags.

**`donna backup`** — manual SQLite backup; reads `DONNA_DB_PATH` and writes to `DONNA_BACKUP_DIR` (default `/donna/backups`). No flags.

**`donna setup`** — interactive wizard (`spec_v3.md §29`).

| Flag | Default | Notes |
|------|---------|-------|
| `--phase` | prompted | `1` / `2` / `3` / `4` |
| `--reconfigure STEP_ID` | — | re-run a specific step (e.g. `discord_channels`) |
| `--dry-run` | off | show what would be configured without writing |

**`donna test-notification`** — push a test message through the live `NotificationService`.

| Flag | Default | Notes |
|------|---------|-------|
| `--config-dir` | `config` | — |
| `--type` | — | **required** (`digest`, `automation_alert`, …) |
| `--channel` | `tasks` | `tasks` / `digest` / `debug` |
| `--content` | — | **required** — message body |
| `--priority` | `3` | `1`–`5` |

### B. Config Files

All 17 live under [`config/`](config/).

| File | Purpose |
|------|---------|
| `agents.yaml` | Agent roster (PM, Scheduler, Research, Coding, Challenger, Communication), autonomy levels, per-phase allowed tools |
| `auth.yaml` | IP gate, Immich integration, email verification, device tokens, bootstrap admin |
| `automations.yaml` | Per-class cadence policies; Discord NL default min interval |
| `calendar.yaml` | Google Calendar IDs, sync polling, time-window rules |
| `capabilities.yaml` | Seed capabilities (product_watch, news_check, email_triage, digest, prep_research, task_decompose, extract_preferences) |
| `chat.yaml` | Chat session TTL, context budget, escalation threshold, intent classification, Discord binding |
| `dashboard.yaml` | UI thresholds: quality-score levels, budget alerts, anomaly flags |
| `discord.yaml` | Bot commands + proactive prompts (evening check-in, stale detection, post-meeting capture, inactivity) |
| `donna_models.yaml` | Model routing, cost tracking, Ollama settings, task-type → model map |
| `email.yaml` | Gmail OAuth, forwarding alias, digest schedules (morning/EOD) |
| `llm_gateway.yaml` | Queue scheduling, rate limits, priority map, per-caller budget, Ollama health checks |
| `memory.yaml` | (slice 12) Vault root, git author, safety envelope (path allowlist, max bytes, sensitive-key refusal), ignore globs; embedding/retrieval/sources blocks parseable but unused until slice 13+ |
| `preferences.yaml` | Learned-preference rules: weekly extraction, confidence threshold |
| `skills.yaml` | Skill-system tuning: enabled, matching confidence, promotion thresholds, auto-draft caps, `nightly_run_hour_utc: 3`, cost budgets |
| `sms.yaml` | Twilio: rate limit 10/day, escalation ladder (30m/60m/120m), blackout hours |
| `task_states.yaml` | State machine: states, transitions, triggers, side effects |
| `task_types.yaml` | Task type registry: models, prompts, schemas, tools, shadow models |

### C. Docker Services

Eight services across six compose files. All attach to the external `homelab` network.

| Service | Compose file | Port | Phase | Image / Build |
|---------|--------------|------|-------|---------------|
| `donna-orchestrator` | `docker/donna-core.yml` | 8100 | 1 | `Dockerfile.orchestrator` |
| `donna-loki` | `docker/donna-monitoring.yml` | 3100 | 1 | `grafana/loki:2.9.0` |
| `donna-promtail` | `docker/donna-monitoring.yml` | (internal) | 1 | `grafana/promtail:2.9.0` |
| `donna-grafana` | `docker/donna-monitoring.yml` | 3000 | 1 | `grafana/grafana:10.2.0` |
| `donna-vault` | `docker/donna-vault.yml` | 8500 | 2 (slice 12) | `caddy:2.8` — WebDAV frontend for the markdown vault |
| `donna-ollama` | `docker/donna-ollama.yml` | 11434 | 3 | official `ollama/ollama` |
| `donna-api` | `docker/donna-app.yml` | 8200 | 4 | `Dockerfile.api` (read-only SQLite) |
| `donna-ui` | `docker/donna-ui.yml` | 8400 | 4 | built from sibling repo `../donna-ui` |

Caddy is run outside this repo; see [`docker/caddy/donna.Caddyfile.example`](docker/caddy/donna.Caddyfile.example).

### D. Alembic Migrations

34 migrations in [`alembic/versions/`](alembic/versions/). `alembic upgrade head` applies them all. Grouped by area:

| Area | Count | Representative files |
|------|------:|----------------------|
| Initial schema | 1 | `6c29a416f050_initial_schema.py` |
| Skill system (Phases 1–3) | 8 | `add_skill_system_phase_1.py`, `add_skill_run_tables_phase_2.py`, `add_lifecycle_tables_phase_3.py`, `seed_skill_system_phase_1.py`, `promote_seed_skills_to_shadow_primary.py`, `f2a3b4c5d6e7_skill_candidate_status_claude_native.py`, `c5d6e7f8a9b0_skill_candidate_report_reasoning.py`, `seed_fetch_and_summarize.py` |
| Automations (Phase 5) | 4 | `add_automation_tables_phase_5.py`, `add_automation_state_blob.py`, `a3b4c5d6e7f8_automation_active_cadence.py`, `b4c5d6e7f8a9_automation_unique_user_name.py` |
| Auth | 3 | `add_auth_tables.py`, `merge_auth_and_skill_system_heads.py`, `merge_capability_tools_and_skill_id_heads.py` |
| Chat | 3 | `add_chat_tables.py`, `add_context_budget_columns.py`, `42bdc9502b1b_merge_chat_context_budget_heads.py` |
| Capabilities | 3 | `b7c8d9e0f1a2_capability_tools_json.py`, `e7f8a9b0c1d2_task_capability_and_inputs.py`, `d6e7f8a9b0c1_seed_claude_native_capability.py` / `seed_claude_native_capabilities.py` / `f3a4b5c6d7e8_seed_news_check_and_email_triage.py` / `seed_product_watch_capability.py` |
| Calendar / scheduling | 2 | `add_calendar_mirror.py`, `add_calendar_mirror_user_id.py` |
| Notifications | 2 | `add_sms_escalation.py`, `add_nudge_events_and_task_stats.py` |
| LLM gateway | 2 | `add_llm_gateway_columns.py`, `b9d2e4f6a135_add_skill_id_to_invocation_log.py` |
| Misc / merges | remainder | `add_manual_draft_at.py`, `add_fixture_tool_mocks.py`, `e1f2a3b4c5d6_merge_heads_for_wave3.py` |

### E. Environment Variables (by phase)

| Phase | Variable | Purpose |
|-------|----------|---------|
| 1 | `ANTHROPIC_API_KEY` | Claude cloud LLM |
| 1 | `DISCORD_BOT_TOKEN` | Discord bot auth |
| 1 | `DISCORD_GUILD_ID` | Server ID |
| 1 | `DISCORD_TASKS_CHANNEL_ID` | Tasks channel |
| 1 | `DISCORD_DIGEST_CHANNEL_ID` | Digest channel |
| 1 | `DISCORD_AGENTS_CHANNEL_ID` | Agent status channel |
| 1 | `DISCORD_DEBUG_CHANNEL_ID` | Debug channel |
| 1 | `DONNA_DATA_PATH` | Root for on-disk storage (default `/donna`) |
| 1 | `DONNA_DB_PATH` | SQLite file / directory (default `/donna/db`) |
| 1 | `DONNA_LOGS_DB_PATH` | Optional logs DB path for `donna health` |
| 1 | `DONNA_WORKSPACE_PATH` | Agent scratch root |
| 1 | `DONNA_BACKUP_PATH` / `DONNA_BACKUP_DIR` | Backup destination |
| 1 | `DONNA_LOG_PATH` | Log archive root |
| 1 | `DONNA_MONTHLY_BUDGET_USD` | Hard monthly cap (default 100.00) |
| 1 | `DONNA_DAILY_PAUSE_THRESHOLD_USD` | Daily autonomous-work pause (default 20.00) |
| 1 | `DONNA_PORT` | Orchestrator HTTP port (default 8100) |
| 1 | `GRAFANA_ADMIN_PASSWORD` | Grafana admin user password |
| 2 | `GOOGLE_CREDENTIALS_PATH` | OAuth client JSON |
| 2 | `GOOGLE_CALENDAR_PERSONAL_ID` | Primary calendar |
| 2 | `GOOGLE_CALENDAR_WORK_ID` | Work calendar |
| 2 | `GOOGLE_CALENDAR_FAMILY_ID` | Family calendar |
| 2 | `TWILIO_ACCOUNT_SID` | Twilio auth |
| 2 | `TWILIO_AUTH_TOKEN` | Twilio auth |
| 2 | `TWILIO_PHONE_NUMBER` | Twilio sending number |
| 2 | `DONNA_USER_PHONE` | SMS recipient |
| 2 | `SUPABASE_URL` | Cloud replica |
| 2 | `SUPABASE_ANON_KEY` | Cloud replica |
| 2 | `SUPABASE_SERVICE_ROLE_KEY` | Cloud replica |
| 2 | `DONNA_VAULT_PATH` | (slice 12) Host path for the Obsidian vault, bind-mounted into the orchestrator |
| 2 | `CADDY_VAULT_USER` | (slice 12) Basic-auth username for the WebDAV endpoint |
| 2 | `CADDY_VAULT_PASSWORD_HASH` | (slice 12) Bcrypt hash for the WebDAV password (never store plaintext) |
| 3 | `DONNA_OLLAMA_GPU_ID` | GPU index for Ollama (RTX 3090) |
| 3 | `IMMICH_ML_GPU_ID` | Optional secondary GPU for Immich ML |
| 4 | `IMMICH_ADMIN_API_KEY` | Required — allowlist sync auth |
| 4 | `DONNA_BOOTSTRAP_ADMIN_EMAIL` | First verifier auto-promoted to admin |
| 4 | `DONNA_CORS_ORIGINS` | Concrete CORS allowlist (`*` refused at startup) |

### F. Running Tests

```bash
pytest tests/unit/           # fast, no external deps
pytest tests/integration/    # requires running services
pytest -m "not slow"         # skip slow tests
pytest -m "not llm"          # skip tests that call LLMs

ruff check src/ tests/
mypy src/donna/
```

### G. Troubleshooting

| Problem | Solution |
|---------|----------|
| **Discord bot won't start** | Verify `DISCORD_BOT_TOKEN`. Ensure all 3 Privileged Gateway Intents are enabled. Confirm the bot is invited to the server. |
| **`donna: command not found`** | Activate the venv: `source .venv/bin/activate`, or reinstall with `pip install -e ".[dev]"`. |
| **Alembic head mismatch** | `alembic current` to inspect, `alembic upgrade head` to apply. If corrupted, restore the DB from a backup. |
| **Docker container exits immediately** | `docker logs <container>`. Common causes: missing env vars, port conflicts, `/donna/db` ownership. |
| **API refuses to start: missing Immich key** | `IMMICH_ADMIN_API_KEY` is required in Phase 4. Set it in `docker/.env` and restart `donna-api`. |
| **API refuses to start: CORS wildcard** | `DONNA_CORS_ORIGINS=*` is rejected because auth uses cookies. Provide a concrete allowlist. |
| **Caddy returns 502** | Caddy container must be on the `homelab` network so it can resolve `donna-api` / `donna-ui` by name. Its source IP must fall inside the `trusted_proxies` CIDR in `config/auth.yaml`. |
| **Supabase sync failures** | Verify `SUPABASE_URL` and keys. If the free tier paused, run the keepalive cron. |
| **Cost budget exceeded** | Autonomous work pauses at `DONNA_DAILY_PAUSE_THRESHOLD_USD`. Inspect `invocation_log` for runaway calls; adjust the threshold or wait until the next day. |
| **OAuth token expired** | Delete `/donna/config/google_token.json` and restart; re-authorise in the browser. |
| **Port conflicts** | `ss -tlnp \| grep <port>`; change the port mapping in the relevant compose file. |
| **Ollama out-of-memory on pull** | Confirm the GPU at `DONNA_OLLAMA_GPU_ID` has enough VRAM for `q6_K`. A q4_K_M variant is smaller but is not the production model. |

### H. Further Reading

- [`spec_v3.md`](spec_v3.md) — canonical design
- [`SETUP.md`](SETUP.md) — install walkthrough
- [`INSTALL_DAY.md`](INSTALL_DAY.md) — hardware install-day playbook
- [`RECOVERY.md`](RECOVERY.md) — backup and disaster recovery
- [`CLAUDE.md`](CLAUDE.md) — contributor conventions
- [`docs/architecture/overview.md`](docs/architecture/overview.md) — architecture tour
- [`docs/domain/`](docs/domain/) — per-subsystem narrative (task system, skills, notifications, etc.)
- [`docs/workflows/`](docs/workflows/) — how-tos (add a skill, run evals, handle a budget breach)
- [`docs/operations/`](docs/operations/) — day-two operations (backup, migrations, Docker, budget)
