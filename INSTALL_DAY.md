# Donna — Install Day Checklist

Personal runbook for the one-time NVMe install and volume migration from the
current dev location (`/home/feuer/Documents/Projects/donna`) to the production
target (`/donna/workspace/donna`). For the full setup reference and command
details, see [SETUP.md](SETUP.md). If anything goes sideways, see
[RECOVERY.md](RECOVERY.md).

Work top to bottom. Check each box as you go. Don't skip the "Before install
day" section — it prevents most of the ways this can get painful.

---

## Before install day (do this ahead of time)

- [ ] All install-prep PRs merged to `main`
- [ ] `cd /home/feuer/Documents/Projects/donna && git checkout main && git pull`
      — so the source clone that gets copied has the latest `main`
- [ ] Confirm `docker/.env` in the source clone is fully populated:
      `grep -c "<FILL_IN" docker/.env` returns `0`
- [ ] Confirm `docker/google_credentials.json` exists in the source clone
- [ ] Back up any local DB state worth keeping:
      `cp /home/feuer/Documents/Projects/donna/*.db ~/donna-db-snapshot/`
      (skip if pre-first-run)
- [ ] Charge your phone — you'll need it for the Google OAuth flow and the
      Twilio verified caller ID check during smoke tests

## Hardware / OS

- [ ] NVMe physically installed
- [ ] Kernel sees the device: `lsblk` shows the new device (likely `/dev/nvme0n1`
      or `/dev/nvme1n1`)
- [ ] Partition + format as ext4:
      `sudo mkfs.ext4 -L donna /dev/nvmeXnY`
- [ ] Add to `/etc/fstab` with `noatime` (matters for SQLite write latency):
      `LABEL=donna  /donna  ext4  defaults,noatime  0  2`
- [ ] Mount: `sudo mkdir -p /donna && sudo mount /donna`
- [ ] Verify: `df -h /donna` shows the expected size

## Filesystem layout

```bash
sudo mkdir -p /donna/{db,workspace,config,prompts,fixtures,logs/archive}
sudo mkdir -p /donna/backups/{daily,weekly,monthly,offsite}
sudo chown -R $USER:$USER /donna
```

- [ ] Directories created
- [ ] `ls -la /donna` shows `$USER:$USER` ownership on every subdir

## Repo migration

```bash
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  /home/feuer/Documents/Projects/donna/ /donna/workspace/donna/
cd /donna/workspace/donna
git worktree prune     # clears stale absolute-path worktree refs from the source
git status             # should be clean, branch `main`
git remote -v          # origin → github.com/nfeuer/donna.git
```

- [ ] rsync completed without errors
- [ ] Clean working tree, branch `main`, remote intact
- [ ] Supabase fix landed:
      `grep SUPABASE_SERVICE_ROLE_KEY src/donna/integrations/supabase_sync.py`
      returns the matching line (not `SUPABASE_KEY`)

## Secrets

Both files should already be present from the `rsync` (they were staged in the
source clone during install-prep).

- [ ] `docker/.env` present, no `<FILL_IN>` placeholders
- [ ] `docker/google_credentials.json` present
- [ ] Move OAuth JSON to its runtime home and lock it down:
      ```bash
      sudo mv docker/google_credentials.json /donna/config/google_credentials.json
      chmod 600 /donna/config/google_credentials.json
      chmod 600 docker/.env
      ```
- [ ] `GOOGLE_CREDENTIALS_PATH` in `.env` matches the file's new location
      (`/donna/config/google_credentials.json`) — no edit needed, but verify

## Python environment

See [SETUP.md §4](SETUP.md) for the authoritative install steps.

- [ ] Fresh venv: `python3.12 -m venv .venv && source .venv/bin/activate`
- [ ] `pip install -U pip`
- [ ] Editable install per SETUP.md §4
- [ ] Smoke test: `donna --help` runs without import errors

## Database

See [SETUP.md §6](SETUP.md) for the full sequence.

- [ ] `alembic upgrade head` — creates schemas at `/donna/db/donna_tasks.db`
      and `/donna/db/donna_logs.db`
- [ ] `sqlite3 /donna/db/donna_tasks.db ".tables"` shows the expected tables

## First-time Google OAuth

Donna's Google auth is triggered on first run of the orchestrator — there's no
separate `donna auth` subcommand. See [SETUP.md §5](SETUP.md) (the "Google
Credentials" subsection) for the exact flow.

- [ ] Start the orchestrator in dev mode: `donna run --dev`
- [ ] Browser opens a Google consent screen — sign in as
      **`donna.messaging@gmail.com`** (NOT `nickfeuer@gmail.com`)
- [ ] Approve all 4 scopes:
      `gmail.readonly`, `gmail.compose`, `calendar`, `calendar.readonly`
- [ ] Refresh token written to `/donna/config/` (check with `ls /donna/config/`)
- [ ] Stop the orchestrator (`Ctrl-C`) — this was just to bootstrap auth

## Docker stack

- [ ] `docker compose -f docker/donna-core.yml up -d`
- [ ] `docker compose -f docker/donna-monitoring.yml up -d`
- [ ] `docker compose ps` — all services report `healthy` or `running`
- [ ] Grafana reachable at http://localhost:3000, login `admin` /
      `GRAFANA_ADMIN_PASSWORD` from `.env`

## Smoke tests

See [SETUP.md §13 (Verification Checklist)](SETUP.md) for the full list. Minimum
required:

- [ ] `donna health` reports all subsystems green
- [ ] Discord bot appears online in the guild
- [ ] Send `!ping` (or whatever the bot's ping command is) in the Discord
      `agents` channel — bot responds
- [ ] Create a test task via Discord DM to the bot — task appears in
      `/donna/db/donna_tasks.db` (verify with `sqlite3`)
- [ ] Check Grafana → **LLM Gateway** dashboard → the model call from the
      previous step shows up with cost, latency, and token counts
- [ ] Check Grafana → task dashboard → the test task is visible

## Deferred post-install (do later, not on install day)

- [ ] **Twilio Toll-Free Verification.** Required before upgrading off the
      trial account, otherwise SMS will start getting carrier-filtered. Form
      is under Twilio Console → **Phone Numbers → Regulatory Compliance →
      Toll-Free Verification**. Business type: *Sole Proprietor*. Use case:
      *"Personal AI assistant sending task reminders, calendar alerts, and
      status updates to the account owner only. Single recipient: the
      account owner's verified phone number."* Takes 3–5 business days.
- [ ] **Rotate secrets that were shared in chat transcripts during
      install-prep.** The install-prep session logged each credential as it
      was entered. Rotate defensively even though the transcripts are local:
  - [ ] Anthropic API key (console.anthropic.com → API Keys → rotate)
  - [ ] Discord bot token (Discord Developer Portal → Bot → Reset Token)
  - [ ] Supabase service_role key (Project Settings → API → Roll)
  - [ ] Twilio auth token (Console → Account → API keys & tokens → rotate)
  - [ ] Grafana admin password is local-only, no rotation unless the machine
        itself is compromised
- [ ] **Automated backups** to the `/donna/backups/offsite/` target, per
      [SETUP.md §6](SETUP.md)
- [ ] **Decommission the old source clone** at
      `/home/feuer/Documents/Projects/donna` once `/donna/workspace/donna`
      is verified working end to end for a full day

## If something goes wrong

- First stop: [RECOVERY.md](RECOVERY.md)
- Common issue: SQLite WAL files locked after a rough shutdown —
  `.db-wal` and `.db-shm` files next to the main `.db` are normal, but if
  Donna won't start, stop all containers first, then restart
- OAuth refresh token expired or revoked: delete the cached token file in
  `/donna/config/` and rerun `donna run --dev` to re-trigger the flow
- Twilio SMS delivery failing with error 30034 or 30032: you upgraded off
  trial without doing TFV — either downgrade back to trial or submit the
  TFV form
