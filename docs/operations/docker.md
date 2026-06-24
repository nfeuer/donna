# Docker

Donna uses a multi-file compose pattern so services can be brought up and
down independently. Design reference:
[`spec_v3.md` §3.5.1 Docker Compose Structure](../reference-specs/spec-v3.md).

## Compose Files

| File | Services |
|---|---|
| `docker/donna-core.yml` | Orchestrator, Discord bot, SQLite volume |
| `docker/donna-monitoring.yml` | Grafana, Loki, Promtail |
| `docker/donna-ollama.yml` | Ollama (GPU pinned to RTX 3090) |
| `docker/donna-app.yml` | FastAPI backend for the Flutter client |

## Bring-Up

```bash
# Full stack
docker compose \
  -f docker/donna-core.yml \
  -f docker/donna-monitoring.yml \
  -f docker/donna-ollama.yml \
  -f docker/donna-app.yml \
  up -d --build
```

Helper scripts: `scripts/donna-up.sh`, `scripts/donna-down.sh`.

> **Production deploys run from a committed snapshot — see [Deployment](deployment.md).**
> `docker compose … up` here targets the live repo and is for local/dev use.

## Config Volume

The orchestrator reads configuration from a mounted volume rather than
the baked-in `/app/config` directory. The Dockerfile CMD includes
`--config-dir /donna/config`, and the compose file mounts the repo's
`config/` directory there:

```yaml
volumes:
  - ../config:/donna/config   # writable — OAuth token refresh writes here
```

The mount is **writable** (no `:ro`) so the Google Calendar client can
refresh its OAuth token and write the updated `token.json` back to disk.
Relative paths in `calendar.yaml` (e.g. `token_path: "token.json"`) are
resolved against this config directory at load time.

## Environment

Copy `docker/.env.example` to `docker/.env` and fill in:

- `ANTHROPIC_API_KEY`
- `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_USER_ID`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- Google API OAuth credentials
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`

## Google Calendar OAuth in Docker

The Google Calendar integration uses OAuth2, which requires a one-time
interactive browser consent flow. Docker containers run headless and
cannot open a browser, so the token must be provisioned locally first.

1. **Generate the token on the host:**

```bash
python -m donna.integrations.calendar config/
```

This opens a browser for Google consent and writes `token.json`.

2. **Mount the token into Docker:**

Ensure your compose file mounts the directory containing `token.json`
at the path matching `GOOGLE_CREDENTIALS_PATH` in `.env`.

3. **Set `DONNA_HEADLESS=true`** in `docker/.env` (already the default in
`.env.example`). When this is set, the calendar client uses the
pre-provisioned token and raises a clear error if it is missing or
expired rather than attempting a browser flow.

Token refresh is automatic — the client refreshes expired tokens using
the stored refresh token and writes the updated token back to disk.

## GPU

Per `spec_v3.md §3.5.2`, the RTX 3090 is dedicated to Donna/Ollama. GTX
1080 remains with Immich. No GPU sharing between workloads.
