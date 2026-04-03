# Donna — Implementation Guide

## Overview

Donna is an AI personal assistant that actively manages tasks, schedules, reminders, and delegates work to sub-agents. Named after Donna Paulsen from *Suits* — sharp, confident, efficient, never sycophantic.

**Architecture:** Hub-and-spoke. A central orchestrator routes work to specialized sub-agents (parser, classifier, scheduler, nudger) via a model abstraction layer. All LLM calls flow through `complete(prompt, schema, model_alias)` — never directly to a provider. Tool calls are proposed by models and validated/executed by the orchestrator.

**Primary interface:** Discord bot. Secondary channels: Twilio SMS/voice, Gmail (draft-only), REST API (Flutter app).

**Data:** SQLite on NVMe (WAL mode) with async Supabase Postgres replica. All API calls tracked in `invocation_log` for cost management.

See [docs/architecture.md](docs/architecture.md) for the full architectural deep dive.

---

## Phase 0: Prerequisites

### 0.1 Hardware

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Linux 64-bit | Ubuntu 22.04+ |
| RAM | 4 GB | 8 GB+ |
| Storage | 10 GB free | NVMe preferred |
| GPU | None (Phase 1–2) | RTX 3090 (Phase 3+) |

### 0.2 Software

Install required tools:

```bash
# Python 3.12+
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3.12-dev

# Docker Engine + Compose v2
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker

# Git + curl
sudo apt install -y git curl
```

> **Windows users:** Install [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and run all commands inside an Ubuntu 22.04 distribution.

### 0.3 External Accounts

| Service | Purpose | When Needed | Cost |
|---------|---------|-------------|------|
| Anthropic | Core LLM | Phase 1 (required) | Pay-per-use |
| Discord | Primary chat | Phase 1 (required) | Free |
| Twilio | SMS/Voice escalation | Phase 2 (optional) | Pay-per-use |
| Google Cloud | Calendar + Gmail | Phase 2 (optional) | Free |
| Supabase | Cloud Postgres replica | Phase 2 (optional) | Free tier |
| Firebase | REST API auth | Phase 4 (optional) | Free tier |

#### Anthropic

1. Go to [console.anthropic.com](https://console.anthropic.com).
2. Create an account or sign in.
3. Navigate to **API Keys** → **Create Key**.
4. Copy the key (starts with `sk-ant-`). You will need it for `.env` later.

#### Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**.
2. **Bot** tab → **Add Bot**.
3. Enable all 3 **Privileged Gateway Intents**:
   - Presence Intent
   - Server Members Intent
   - Message Content Intent
4. **Reset Token** → copy the token.
5. **OAuth2** → **URL Generator**:
   - Scopes: `bot`
   - Permissions: Send Messages, Read Message History, View Channels, Add Reactions
   - Open the generated URL → invite bot to your server.
6. **Enable Developer Mode** in Discord (User Settings → Advanced → Developer Mode).
7. Right-click your server → **Copy Server ID** (this is `DISCORD_GUILD_ID`).
8. Right-click each channel → **Copy Channel ID** for tasks, digest, agents, and debug channels.

#### Twilio (Phase 2+)

1. Sign up at [twilio.com](https://www.twilio.com).
2. From the Console dashboard, copy:
   - **Account SID**
   - **Auth Token**
   - **Phone Number** (buy one if needed)

#### Google Cloud (Phase 2+)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a new project.
2. **APIs & Services** → **Library** → enable:
   - Google Calendar API
   - Gmail API
3. **Credentials** → **Create Credentials** → **OAuth client ID** → Application type: **Desktop app** → download the JSON file.
4. Place it at `/donna/config/google_credentials.json` (or wherever `$GOOGLE_CREDENTIALS_PATH` points).
5. Find Calendar IDs: Google Calendar → three-dot menu on each calendar → **Settings and sharing** → **Calendar ID**.

#### Supabase (Phase 2+)

1. Go to [supabase.com](https://supabase.com) → create a free project.
2. **Settings** → **API** → copy:
   - **Project URL**
   - **anon key**
   - **service_role key**
3. Schema is created automatically by write-through sync on first orchestrator run.

#### Firebase (Phase 4 only)

1. Go to [console.firebase.google.com](https://console.firebase.google.com) → **Add project**.
2. Skip Google Analytics (optional).
3. **Authentication** → **Get Started** → **Sign-in method** → enable **Email/Password**.
4. **Project Settings** (gear icon) → copy the **Project ID**.
5. After the first user signs in: **Authentication** → **Users** tab → copy the **UID**.

---

## Phase 1: Core System (Required)

### 1.1 Clone the Repository

```bash
git clone <repo-url> donna
cd donna
```

### 1.2 Create Storage Directories

```bash
sudo mkdir -p /donna/{db,workspace,backups/{daily,weekly,monthly,offsite},logs/archive,config,prompts,fixtures,models}
sudo chown -R $USER:$USER /donna
```

Directory layout:

```
/donna/
├── db/                  # SQLite databases (donna_tasks.db, donna_logs.db)
├── workspace/           # Agent scratch space
├── backups/
│   ├── daily/
│   ├── weekly/
│   ├── monthly/
│   └── offsite/
├── logs/
│   └── archive/
├── config/              # Runtime config, OAuth tokens
├── prompts/             # Externalized prompt templates
├── fixtures/            # Evaluation test fixtures
└── models/              # Local model cache (Phase 3)
```

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

Verify the installation:

```bash
donna --help
```

### 1.4 Environment Configuration

```bash
cp docker/.env.example docker/.env
```

Fill in the **minimum required** variables:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_GUILD_ID` | Your Discord server ID |
| `DISCORD_TASKS_CHANNEL_ID` | Channel for task messages |
| `DISCORD_DIGEST_CHANNEL_ID` | Channel for daily digests |
| `DONNA_MONTHLY_BUDGET_USD` | Monthly cost cap (default: 100.00) |
| `DONNA_DAILY_PAUSE_THRESHOLD_USD` | Daily pause threshold (default: 20.00) |

Storage paths default to `/donna/` and do not need editing if you followed §1.2.

### 1.5 Database Setup

```bash
alembic upgrade head
```

This applies all migrations (initial schema, calendar mirror, SMS escalation, nudge events). The database is created at `donna_tasks.db` in the current directory. Docker uses `$DONNA_DB_PATH`.

Verify:

```bash
alembic current    # shows latest revision hash
alembic history    # lists all migration versions
```

### 1.6 Review Config Files

These ship with sensible defaults. No edits needed for Phase 1, but familiarize yourself:

| File | Purpose |
|------|---------|
| `config/donna_models.yaml` | Model routing, cost limits, shadow config |
| `config/task_types.yaml` | Task pipeline definitions |
| `config/task_states.yaml` | State machine (backlog → scheduled → in_progress → done) |
| `config/preferences.yaml` | User preferences (empty, auto-populated Phase 3) |

### 1.7 Run Locally (Smoke Test)

```bash
export $(grep -v '^#' docker/.env | xargs)
donna run --dev --log-level DEBUG
```

Verify:

```bash
curl http://localhost:8100/health
# Expected: {"status": "ok"}
```

Test Discord: send a message in your tasks channel — Donna should acknowledge it.

### 1.8 Docker Deployment

```bash
docker network create homelab
docker compose -f docker/donna-core.yml --env-file docker/.env up --build -d
```

Check:

```bash
docker ps                              # donna-orchestrator should show Up (healthy)
docker logs donna-orchestrator         # structured JSON logs
curl http://localhost:8100/health      # 200 OK
```

### 1.9 Run Tests

```bash
pytest tests/unit/
pytest tests/integration/
```

### Gate Check: Phase 1

- [ ] `donna --help` lists `run`, `eval`, `health`, `backup`
- [ ] `alembic current` shows latest revision
- [ ] `curl localhost:8100/health` returns 200
- [ ] All unit tests pass
- [ ] Docker container healthy
- [ ] Discord bot responds to messages

---

## Phase 2: Monitoring & Integrations (Recommended)

### 2.1 Monitoring Stack

```bash
docker compose -f docker/donna-monitoring.yml --env-file docker/.env up -d
```

Services started:

| Service | Port |
|---------|------|
| Loki | 3100 |
| Promtail | (internal) |
| Grafana | 3000 |

Access Grafana at `http://localhost:3000` with credentials `admin` / `$GRAFANA_ADMIN_PASSWORD`.

Four dashboards are auto-provisioned: **Cost**, **Health**, **Pipeline**, **Errors**.

### 2.2 Google Calendar Integration

1. Place `google_credentials.json` at `$GOOGLE_CREDENTIALS_PATH` (default: `/donna/config/google_credentials.json`).
2. Set calendar IDs in `.env`:
   ```
   GOOGLE_CALENDAR_PERSONAL_ID=primary
   GOOGLE_CALENDAR_WORK_ID=<your-work-calendar-id>
   GOOGLE_CALENDAR_FAMILY_ID=<your-family-calendar-id>
   ```
3. First run opens a browser for OAuth consent.
4. Subsequent runs use the cached token at `/donna/config/google_token.json`.

### 2.3 Gmail Integration

Uses the same credentials as Calendar (Gmail API must be enabled in Google Cloud Console).

Email access is **read-only + draft creation** — no direct sending by default. Configure via `config/email.yaml`.

### 2.4 Twilio SMS/Voice

Set in `docker/.env`:

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX
```

Configure the Twilio webhook to point to: `http://your-server:8100/webhooks/sms`

Configuration: `config/sms.yaml` (rate limit 10/day, escalation tiers).

> Optional in Phase 1. Gracefully disabled if environment variables are unset.

### 2.5 Supabase Cloud Replica

Set in `docker/.env`:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

Schema is created automatically on first sync.

Set up a keepalive cron to prevent free-tier pause:

```bash
crontab -e
# Add:
0 12 * * 1 /path/to/donna/scripts/supabase_keepalive.sh
```

### Gate Check: Phase 2

- [ ] Grafana accessible at `:3000` with dashboards showing data
- [ ] Calendar events visible after sync
- [ ] Supabase dashboard shows active connection
- [ ] SMS test (if configured): send from phone to Twilio number

---

## Phase 3: Local LLM (Optional — Requires RTX 3090)

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

**Verify Docker GPU access:**

```bash
docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi
```

### 3.2 Start Ollama

Set `DONNA_OLLAMA_GPU_ID=1` in `docker/.env` (or your RTX 3090 device index).

```bash
docker compose -f docker/donna-ollama.yml --env-file docker/.env up -d
docker exec donna-ollama nvidia-smi  # verify GPU visible
```

### 3.3 Pull Model

```bash
docker exec donna-ollama ollama pull qwen2.5:32b-instruct-q4_K_M
docker exec donna-ollama ollama list  # confirm downloaded
```

~19 GB download, requires 19–20 GB VRAM.

### 3.4 Smoke Test

```bash
docker exec -it donna-ollama ollama run qwen2.5:32b-instruct-q4_K_M \
  "Extract the task: 'remind me to call the dentist Thursday'. Reply JSON: {\"task\": \"\", \"due\": \"\"}"
```

Check VRAM usage:

```bash
docker exec donna-ollama nvidia-smi --query-gpu=memory.used,memory.free --format=csv
```

### 3.5 Evaluation Harness

```bash
donna eval --task-type task_parse --model ollama/qwen2.5:32b-instruct-q4_K_M
donna eval --task-type classify_priority --model ollama/qwen2.5:32b-instruct-q4_K_M
```

Pass gates:
- **Tier 1** ≥ 90%
- **Tier 2** ≥ 80%
- **Tier 3** ≥ 60%

### 3.6 Shadow Mode (1–2 Weeks)

Edit `config/donna_models.yaml` — add a `shadow` block under `parser`:

```yaml
models:
  parser:
    provider: anthropic
    model: claude-sonnet-4-20250514
    shadow:
      provider: ollama
      model: qwen2.5:32b-instruct-q4_K_M
```

Restart the orchestrator. Shadow outputs are logged but **not** used for responses.

### 3.7 Switch to Hybrid Routing

After shadow quality is confirmed, update `config/donna_models.yaml`:

```yaml
models:
  parser:
    provider: ollama
    model: qwen2.5:32b-instruct-q4_K_M
    estimated_cost_per_1k_tokens: 0.0001
  reasoner:
    provider: anthropic
    model: claude-sonnet-4-20250514
  fallback:
    provider: anthropic
    model: claude-sonnet-4-20250514
```

Enable spot-check quality monitoring (5% sample rate).

To revert: change `parser.provider` back to `anthropic` and restart.

### Gate Check: Phase 3

- [ ] `curl localhost:11434/api/tags` lists the model
- [ ] Eval harness passes Tier 1 + Tier 2 gates
- [ ] VRAM usage ~19–20 GB
- [ ] Shadow mode logging visible in `invocation_log`

---

## Phase 4: REST API Backend (Optional — For Flutter App)

### 4.1 Firebase Project Setup

Follow the Firebase steps in [Phase 0 §0.3](#firebase-phase-4-only) above.

### 4.2 Environment Variables

Add to `docker/.env`:

```
FIREBASE_PROJECT_ID=your-project-id
DONNA_DEFAULT_USER_ID=nick
DONNA_USER_MAP=
DONNA_AUTH_DISABLED=true
```

### 4.3 Deploy API Container

```bash
docker compose -f docker/donna-app.yml --env-file docker/.env up --build -d
```

### 4.4 Verify

```bash
curl http://localhost:8200/health           # 200 OK
curl http://localhost:8200/tasks            # works with auth disabled
```

### 4.5 Enable Authentication

After the first Flutter user signs in:

1. Firebase Console → **Authentication** → **Users** → copy the **UID**.
2. Set `DONNA_USER_MAP=<firebase-uid>:nick` in `docker/.env`.
3. Set `DONNA_AUTH_DISABLED=false`.
4. Restart:

```bash
docker compose -f docker/donna-app.yml --env-file docker/.env up -d
```

### Gate Check: Phase 4

- [ ] `curl localhost:8200/health` returns 200
- [ ] Task CRUD works through API
- [ ] Auth-protected endpoints reject unauthenticated requests

---

## Appendices

### A. CLI Reference

| Command | Description |
|---------|-------------|
| `donna run` | Start the orchestrator |
| `donna run --dev --log-level DEBUG` | Development mode with verbose logging |
| `donna eval --task-type <type> --model <alias>` | Run evaluation harness |
| `donna health` | Check system health |
| `donna backup` | Trigger manual backup |

### B. Running Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests (requires running services)
pytest tests/integration/

# Specific markers
pytest -m "not slow"        # skip slow tests
pytest -m "not llm"         # skip tests that call LLMs

# Linting + type checking
ruff check src/ tests/
mypy src/donna/
```

### C. Troubleshooting

| Problem | Solution |
|---------|----------|
| **Discord bot won't start** | Verify `DISCORD_BOT_TOKEN` is correct. Ensure all 3 Privileged Gateway Intents are enabled. Check bot is invited to the server. |
| **`donna: command not found`** | Activate the venv: `source .venv/bin/activate`. Or reinstall: `pip install -e ".[dev]"`. |
| **Alembic errors** | Run `alembic current` to check state. If head mismatch, run `alembic upgrade head`. If corrupted, restore DB from backup. |
| **Docker container exits immediately** | Check `docker logs donna-orchestrator`. Common causes: missing env vars, port conflicts, DB path permissions. |
| **Supabase sync failures** | Verify `SUPABASE_URL` and keys. Check free-tier project hasn't paused (run keepalive cron). |
| **Cost budget exceeded** | Autonomous agent work pauses at `$DONNA_DAILY_PAUSE_THRESHOLD_USD`. Check `invocation_log` for runaway calls. Adjust threshold or wait until next day. |
| **OAuth token expired** | Delete `/donna/config/google_token.json` and restart. Re-authorize in browser. |
| **Port conflicts** | Check with `ss -tlnp | grep <port>`. Change port mappings in the relevant Compose file. |

### D. Further Reading

- [SETUP.md](SETUP.md) — Quick-start setup reference
- [RECOVERY.md](RECOVERY.md) — Backup and disaster recovery procedures
- [docs/architecture.md](docs/architecture.md) — Detailed system architecture
