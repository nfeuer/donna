# Donna — Setup Guide

This guide covers everything needed to get Donna running, from a fresh machine to a fully operational deployment. Follow the sections in order.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Clone the Repository](#2-clone-the-repository)
3. [Create Storage Directories](#3-create-storage-directories)
4. [Python Environment](#4-python-environment)
5. [Environment Configuration](#5-environment-configuration)
   - [Anthropic API](#51-anthropic-api)
   - [Discord Bot](#52-discord-bot)
   - [Twilio SMS/Voice](#53-twilio-smsvoice)
   - [Google APIs](#54-google-apis-calendar--gmail)
   - [Supabase](#55-supabase)
   - [Storage Paths](#56-storage-paths)
   - [Grafana](#57-grafana)
   - [GPU (Phase 3+)](#58-gpu-phase-3-only)
6. [Database Setup](#6-database-setup)
7. [Config Files](#7-config-files)
8. [Running Locally (Dev Mode)](#8-running-locally-dev-mode)
9. [Docker Deployment](#9-docker-deployment)
10. [Monitoring Stack](#10-monitoring-stack)
11. [Verification Checklist](#11-verification-checklist)
12. [Running Tests](#12-running-tests)
13. [CLI Reference](#13-cli-reference)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

### Hardware

| Component | Minimum | Notes |
|-----------|---------|-------|
| OS | Linux (64-bit) | Ubuntu 22.04+ recommended. Homelab server preferred for 24/7 operation. |
| RAM | 4 GB | 8 GB+ recommended for Docker + monitoring stack |
| Storage | NVMe drive (separate) | Dedicated 1TB NVMe for `/donna/` data path. Regular disk works but NVMe is preferred for sub-ms SQLite reads. |
| GPU | None required for Phase 1–2 | RTX 3090 (24 GB VRAM) required for Phase 3+ local LLM via Ollama |

### Software

Install these before proceeding:

```bash
# Python 3.12 or newer
python3 --version   # must be 3.12+

# Docker Engine + Docker Compose v2
docker --version           # 24.0+
docker compose version     # 2.20+

# Git
git --version

# curl (used by Docker health checks)
curl --version
```

**Ubuntu installation commands:**

```bash
# Python 3.12
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev python3-pip

# Docker (official method)
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add your user to the docker group (avoids needing sudo)
sudo usermod -aG docker $USER
newgrp docker
```

**Windows installation notes:**

Donna is designed for Linux. On Windows:
- Install [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) with WSL 2 backend enabled
- Run all commands inside a WSL 2 terminal (Ubuntu recommended)
- Python and all tooling should be installed inside WSL, not on the Windows host
- Storage paths (`/donna/`) must be WSL filesystem paths, not Windows paths (`C:\...`)
- Git should also be installed inside WSL: `sudo apt install git`

### External Accounts Needed

Before starting, create accounts or projects on each of these:

| Service | Purpose | Cost |
|---------|---------|------|
| [Anthropic](https://console.anthropic.com) | Claude API — core LLM | Pay-per-use (~$3/M input tokens) |
| [Discord](https://discord.com/developers) | Primary chat interface | Free |
| [Twilio](https://www.twilio.com) | SMS/Voice escalation | Pay-per-use |
| [Google Cloud](https://console.cloud.google.com) | Calendar + Gmail integration | Free (within quota) |
| [Supabase](https://supabase.com) | Cloud Postgres replica | Free tier |

---

## 2. Clone the Repository

```bash
git clone <repo-url> donna
cd donna
```

---

## 3. Create Storage Directories

Donna uses a dedicated storage tree at `/donna/`. Create it before running anything:

```bash
sudo mkdir -p /donna/{db,workspace,backups/{daily,weekly,monthly,offsite},logs/archive,config,prompts,fixtures,models}
sudo chown -R $USER:$USER /donna
```

**Layout explanation:**

```
/donna/
├── db/              ← SQLite databases (donna_tasks.db, donna_logs.db)
├── workspace/       ← Agent sandboxed working directory
├── backups/
│   ├── daily/       ← 7-day retention
│   ├── weekly/      ← 4-week retention
│   ├── monthly/     ← 3-month retention
│   └── offsite/     ← Staging for cloud sync
├── logs/
│   └── archive/     ← Compressed historical log exports
├── config/          ← Optional: place google_credentials.json here
├── prompts/         ← Externalized prompt templates
├── fixtures/        ← Evaluation test fixtures
└── models/          ← Ollama model cache (Phase 3+)
```

> If you prefer a different root path (e.g. `/mnt/nvme/donna`), that's fine — just update `DONNA_DATA_PATH` and related vars in your `.env` file accordingly.

---

## 4. Python Environment

### Option A — uv (recommended, faster)

The repo ships a `uv.lock` file. If you have `uv` installed:

```bash
pip install uv       # or: curl -Lsf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.12
source .venv/bin/activate
uv sync --extra dev
```

### Option B — pip + venv

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Verify

```bash
donna --help
```

You should see the `run`, `eval`, `health`, and `backup` subcommands listed.

---

## 5. Environment Configuration

Copy the template and open it for editing:

```bash
cp docker/.env.example docker/.env
```

The sections below explain every variable.

---

### 5.1 Anthropic API

```env
ANTHROPIC_API_KEY=sk-ant-...
```

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key under **API Keys**
3. Paste it as the value of `ANTHROPIC_API_KEY`

Cost controls (adjust if needed, but defaults are sensible):

```env
DONNA_MONTHLY_BUDGET_USD=100.00       # Hard cap — API calls stop at this
DONNA_DAILY_PAUSE_THRESHOLD_USD=20.00 # Autonomous agent work pauses at this
```

Every API call is tracked in the `invocation_log` database table. When the daily threshold is hit, autonomous agent work halts automatically. Tasks that would cost more than $5 each require manual approval (configured in `config/donna_models.yaml` under `task_approval_threshold_usd`).

---

### 5.2 Discord Bot

Donna uses Discord as its primary interactive channel.

#### Step 1: Create a Discord Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. "Donna")
3. Go to the **Bot** tab → click **Add Bot**
4. Under **Privileged Gateway Intents**, enable all three:
   - **Presence Intent**
   - **Server Members Intent**
   - **Message Content Intent** ← required for reading messages
5. Click **Reset Token** and copy the token

```env
DISCORD_BOT_TOKEN=your-token-here
```

#### Step 2: Invite the Bot to Your Server

1. Go to **OAuth2 → URL Generator**
2. Select scopes: `bot`
3. Select bot permissions: `Send Messages`, `Read Message History`, `View Channels`, `Add Reactions`
4. Open the generated URL in your browser and invite the bot to your server

#### Step 3: Get Channel and Guild IDs

Enable Developer Mode in Discord: **Settings → Advanced → Developer Mode**

- Right-click your server name → **Copy Server ID** → `DISCORD_GUILD_ID`
- Right-click each channel → **Copy Channel ID**:

```env
DISCORD_GUILD_ID=your-server-id
DISCORD_TASKS_CHANNEL_ID=    # Channel where you send tasks to Donna
DISCORD_DIGEST_CHANNEL_ID=   # Channel where Donna posts daily digests
DISCORD_AGENTS_CHANNEL_ID=   # Channel for agent activity updates
DISCORD_DEBUG_CHANNEL_ID=    # Channel for debug/error output (optional)
```

The bot will start automatically if `DISCORD_BOT_TOKEN` and `DISCORD_TASKS_CHANNEL_ID` are both set. If either is missing, the bot is disabled and a warning is logged.

---

### 5.3 Twilio SMS/Voice

Used for SMS escalation when Discord messages go unanswered.

1. Sign up at [twilio.com](https://www.twilio.com)
2. From the Console Dashboard, copy:

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-auth-token
TWILIO_PHONE_NUMBER=+15551234567   # Your Twilio phone number (E.164 format)
```

3. Configure the Twilio webhook to point to Donna's SMS endpoint. Once deployed, the endpoint is:
   `http://your-server-ip:8100/webhooks/sms`

> Twilio is optional in Phase 1. If these vars are unset, SMS features are disabled gracefully.

---

### 5.4 Google APIs (Calendar + Gmail)

Donna reads/writes Google Calendar and reads Gmail for task extraction.

#### Step 1: Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g. "Donna Assistant")
3. Go to **APIs & Services → Library**
4. Enable:
   - **Google Calendar API**
   - **Gmail API**

#### Step 2: Create OAuth 2.0 Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Set application type to **Desktop app**
4. Download the JSON credentials file
5. Place it at the path configured in your `.env`:

```env
GOOGLE_CREDENTIALS_PATH=/donna/config/google_credentials.json
```

Copy the downloaded file to that path:

```bash
cp ~/Downloads/client_secret_*.json /donna/config/google_credentials.json
```

#### Step 3: Configure Calendar IDs

```env
GOOGLE_CALENDAR_PERSONAL_ID=primary   # "primary" is your main calendar
GOOGLE_CALENDAR_WORK_ID=              # Optional: work calendar ID
GOOGLE_CALENDAR_FAMILY_ID=            # Optional: family calendar ID
```

To find a calendar ID: open Google Calendar → click the three-dot menu on a calendar → **Settings** → copy the **Calendar ID** shown at the bottom.

> The first time the app runs with Google credentials, it will open a browser window to complete the OAuth flow and save a token. Subsequent runs use the cached token.

---

### 5.5 Supabase

Supabase provides a cloud Postgres replica so data is accessible across devices and as a backup.

1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **Settings → API**
3. Copy:

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

> The free tier pauses after 1 week of inactivity. Run the keep-alive script to prevent this:
> ```bash
> # Add to cron (runs weekly)
> crontab -e
> # Add: 0 12 * * 1 /path/to/donna/scripts/supabase_keepalive.sh
> ```

---

### 5.6 Storage Paths

These must match the directories created in [Step 3](#3-create-storage-directories):

```env
DONNA_DATA_PATH=/donna
DONNA_DB_PATH=/donna/db
DONNA_WORKSPACE_PATH=/donna/workspace
DONNA_BACKUP_PATH=/donna/backups
DONNA_LOG_PATH=/donna/logs
```

If you used a different root (e.g. `/mnt/nvme/donna`), update all five accordingly.

---

### 5.7 Grafana

```env
GRAFANA_ADMIN_PASSWORD=changeme   # Change this to something strong
```

Grafana is accessible at `http://localhost:3000` when the monitoring stack is running.

---

### 5.8 GPU (Phase 3+ only)

Skip this section until you have an RTX 3090 for Ollama. The vars are:

```env
IMMICH_ML_GPU_ID=0     # GTX 1080 — dedicated to Immich/media (if applicable)
DONNA_OLLAMA_GPU_ID=1  # RTX 3090 — dedicated to Donna local LLM
```

GPU assignment prevents VRAM contention between workloads.

---

## 6. Database Setup

Run Alembic migrations to initialize the SQLite database schema:

```bash
alembic upgrade head
```

This applies all three migrations in order:
1. `6c29a416f050` — Initial schema (Task, InvocationLog, CorrectionLog, ConversationContext, LearnedPreferences tables)
2. `add_calendar_mirror` — Calendar sync columns
3. `add_sms_escalation` — SMS escalation tracking columns

The database file is created at `donna_tasks.db` in the current directory (for local dev). In Docker it is written to `$DONNA_DB_PATH/donna_tasks.db`.

WAL mode is enabled automatically by the application on first connect — no manual step needed.

**To check migration status:**

```bash
alembic current    # shows the current revision applied
alembic history    # lists all migrations
```

**To roll back one migration:**

```bash
alembic downgrade -1
```

> Never modify existing migration files. Always create a new migration for schema changes: `alembic revision --autogenerate -m "description"`

---

## 7. Config Files

All config lives in `config/`. These files drive behaviour without code changes. Review them before first run.

### `config/donna_models.yaml`

Controls which model handles which task type and enforces cost limits:

```yaml
models:
  parser:
    provider: anthropic
    model: claude-sonnet-4-20250514
  reasoner:
    provider: anthropic
    model: claude-sonnet-4-20250514
  fallback:
    provider: anthropic
    model: claude-sonnet-4-20250514

cost:
  monthly_budget_usd: 100.00
  daily_pause_threshold_usd: 20.00
  task_approval_threshold_usd: 5.00
  monthly_warning_pct: 0.90
```

No edits needed for Phase 1. Phase 3+: swap `parser` to `provider: ollama` once Ollama is validated.

### `config/task_types.yaml`

Defines each task pipeline (prompt template, output schema, model alias). No edits needed.

### `config/task_states.yaml`

Defines the state machine: valid states (`backlog`, `scheduled`, `in_progress`, `blocked`, `waiting_input`, `done`, `cancelled`) and allowed transitions. No edits needed.

### `config/preferences.yaml`

Empty for Phase 1. Populated automatically in Phase 3 as Donna learns user corrections.

### `config/calendar.yaml`, `config/email.yaml`, `config/sms.yaml`

Integration-specific settings. Edit only if you need to override defaults for your calendar IDs, email filters, or SMS routing rules.

---

## 8. Running Locally (Dev Mode)

Set environment variables (from `docker/.env`) before running:

```bash
export $(grep -v '^#' docker/.env | xargs)
donna run --dev --log-level DEBUG
```

Or use a tool like `direnv` to load the `.env` automatically.

**What starts:**
- aiohttp web server on port `8100`
- Discord bot (if `DISCORD_BOT_TOKEN` + `DISCORD_TASKS_CHANNEL_ID` are set)
- Database connection with WAL mode
- All config loaded from `config/`

**Verify it's running:**

```bash
curl http://localhost:8100/health
```

Expected response: `{"status": "ok"}` (or similar).

**Useful flags:**

| Flag | Description |
|------|-------------|
| `--dev` | Human-readable logs instead of JSON |
| `--log-level DEBUG` | Verbose output including all model calls |
| `--port 8200` | Override default port 8100 |
| `--config-dir /path/to/config` | Use a different config directory |

To stop: `Ctrl+C`

---

## 9. Docker Deployment

Docker is the recommended way to run Donna in production (24/7 on a homelab server).

### Step 1: Create the homelab network

All Donna compose files attach to an external Docker network named `homelab`. Create it once:

```bash
docker network create homelab
```

> If you already have a homelab network from another project, skip this step.

### Step 2: Build and start core services

```bash
docker compose -f docker/donna-core.yml --env-file docker/.env up --build -d
```

This starts `donna-orchestrator` which runs the main process (health server + Discord bot).

**Container details:**
- Image: built from `docker/Dockerfile.orchestrator` (Python 3.12-slim)
- Runs as non-root user `donna`
- Port `8100` exposed for health checks
- Volumes mounted from `.env` paths: `db/`, `workspace/`, `backups/`, `logs/`, `config/`, `prompts/`, `schemas/`
- Restarts automatically unless manually stopped

**Check it's healthy:**

```bash
docker ps                                      # should show donna-orchestrator as Up
docker logs donna-orchestrator                 # view logs
curl http://localhost:8100/health              # health endpoint
```

### Step 3: Updating

```bash
docker compose -f docker/donna-core.yml --env-file docker/.env up --build -d
```

Re-running with `--build` rebuilds the image and restarts the container with zero-downtime for config-only changes.

---

## 10. Monitoring Stack

Optional but strongly recommended. Provides Grafana dashboards, log aggregation via Loki, and log shipping via Promtail.

```bash
docker compose -f docker/donna-monitoring.yml --env-file docker/.env up -d
```

**Services started:**

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| Loki | `donna-loki` | 3100 | Log aggregation backend |
| Promtail | `donna-promtail` | — | Ships Docker container logs to Loki |
| Grafana | `donna-grafana` | 3000 | Dashboard UI |

**Access Grafana:**

Open `http://localhost:3000` in your browser.
- Username: `admin`
- Password: value of `GRAFANA_ADMIN_PASSWORD` from your `.env`

Pre-built dashboards are provisioned automatically from `docker/grafana/dashboards/`:
- **Cost dashboard** — API spend over time, daily/monthly totals
- **Health dashboard** — service uptime, error rates
- **Pipeline dashboard** — task throughput, model latency
- **Error dashboard** — structured error log view

Loki datasource is auto-configured from `docker/grafana/datasources/loki.yaml`.

---

## 11. Verification Checklist

Work through these after setup to confirm everything is wired correctly.

- [ ] `donna --help` lists subcommands
- [ ] `alembic current` shows the latest migration revision
- [ ] `curl http://localhost:8100/health` returns 200
- [ ] `pytest tests/unit/` — all tests pass (no credentials needed)
- [ ] `pytest tests/integration/` — all tests pass (SQLite only, no API calls)
- [ ] Discord: send a message in your tasks channel → Donna acknowledges it
- [ ] `docker ps` shows `donna-orchestrator` with status `healthy`
- [ ] `docker logs donna-orchestrator` shows structured JSON logs with no errors
- [ ] Grafana at `http://localhost:3000` is accessible and shows dashboards
- [ ] Supabase: check the dashboard to confirm the connection is active

---

## 12. Running Tests

```bash
# Unit tests only — no external dependencies, fast
pytest tests/unit/

# Integration tests — uses real SQLite, no API calls
pytest tests/integration/

# Skip tests that call the Claude API (avoids spending money)
pytest -m "not llm"

# Full suite (requires all credentials set)
pytest tests/

# With coverage report
pytest tests/unit/ --cov=src/donna --cov-report=term-missing

# Verbose output
pytest tests/unit/ -v
```

**Test markers:**

| Marker | Description |
|--------|-------------|
| `unit` | No external dependencies |
| `integration` | Uses real SQLite, no API |
| `llm` | Calls Claude API — costs money |
| `slow` | Slow tests, skipped in CI by default |

**Linting and type checking:**

```bash
ruff check src/ tests/       # linter
ruff format src/ tests/      # formatter
mypy src/                    # type checker (strict mode)
```

---

## 13. CLI Reference

```
donna run      Start the orchestrator (web server + Discord bot)
donna health   Check system health (placeholder — Phase 2)
donna backup   Trigger a manual backup (placeholder — Phase 2)
donna eval     Run the evaluation harness (Phase 3+)
```

**`donna run` flags:**

```
--dev                   Human-readable logs (default: JSON)
--log-level LEVEL       DEBUG | INFO | WARNING | ERROR | CRITICAL (default: INFO)
--port PORT             Health server port (default: 8100 or $DONNA_PORT)
--config-dir PATH       Config directory (default: config/)
```

**`donna eval` flags (Phase 3+):**

```
--task-type TYPE        e.g. task_parse, classify_priority
--model MODEL           e.g. ollama/llama3.1:8b-q4
--fixtures-dir PATH     Path to fixtures/ directory (default: fixtures/)
--tier N                Run only tier N (1–4). Default: all tiers with pass gates.
```

---

## 14. Troubleshooting

### Discord bot doesn't start

- Confirm `DISCORD_BOT_TOKEN` and `DISCORD_TASKS_CHANNEL_ID` are both set in your environment
- Check that **Message Content Intent** is enabled in the Discord developer portal (Bot → Privileged Gateway Intents)
- Look for `discord_bot_disabled` in the logs — it will include the reason

### `donna: command not found`

- Make sure your virtual environment is activated: `source .venv/bin/activate`
- Confirm the package installed correctly: `pip show donna`
- If using Docker, the `donna` command runs as the container entrypoint automatically

### Alembic errors on startup

```bash
alembic current    # check what revision is applied
alembic history    # list all known revisions
alembic upgrade head  # apply any pending migrations
```

If you see `sqlite3.OperationalError: database is locked`, another process is connected to the same SQLite file. Only one Donna process should run at a time.

### Docker: container exits immediately

```bash
docker logs donna-orchestrator
```

Common causes:
- Missing required environment variable — check the log for the variable name
- `DONNA_DB_PATH` directory doesn't exist or isn't writable
- Config file missing from `config/` — ensure all YAML files are present

### Docker: network error on `up`

```
network homelab declared as external, but could not be found
```

Fix: `docker network create homelab`

### Supabase connection failures

- The free tier pauses after 1 week of inactivity — log into supabase.com to wake the project, or set up the keep-alive cron job (see [Section 5.5](#55-supabase))
- Check `SUPABASE_URL` includes `https://` and ends with `.supabase.co`

### API cost exceeded

- Check the `invocation_log` table: `sqlite3 /donna/db/donna_tasks.db "SELECT SUM(cost_usd) FROM invocation_log WHERE date(created_at) = date('now');"`
- Autonomous agent work auto-pauses when `DONNA_DAILY_PAUSE_THRESHOLD_USD` is hit
- Monthly spend is tracked against `DONNA_MONTHLY_BUDGET_USD`; a warning is logged at 90%

### Google OAuth token expired

Delete the cached token and re-authenticate:

```bash
rm /donna/config/google_token.json
donna run --dev   # will prompt for OAuth on next startup
```

### Port 8100 already in use

```bash
# Find and kill the process using the port
lsof -i :8100
kill -9 <PID>

# Or run Donna on a different port
donna run --dev --port 8200
```
