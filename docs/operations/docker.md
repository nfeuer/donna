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

## Environment

Copy `docker/.env.example` to `docker/.env` and fill in:

- `ANTHROPIC_API_KEY`
- `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_USER_ID`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- Google API OAuth credentials
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`

## GPU

Per `spec_v3.md §3.5.2`, the RTX 3090 is dedicated to Donna/Ollama. GTX
1080 remains with Immich. No GPU sharing between workloads.
