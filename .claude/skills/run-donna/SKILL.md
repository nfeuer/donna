---
name: run-donna
description: Start, stop, restart, and tail logs for Donna Docker services
disable-model-invocation: true
---

# Run Donna

Manage Donna's Docker Compose stacks. All commands use `COMPOSE_PROJECT_NAME=donna` and the env file at `docker/.env`.

## Usage

The user will say one of: start, stop, restart, logs, status, rebuild. Ask which services if not specified.

## Service Groups

| Group | Script / Compose File | Ports |
|-------|----------------------|-------|
| core | `scripts/donna-up.sh` / `docker/donna-core.yml` | 8100 (health) |
| api | `docker/donna-app.yml` | 8200 (FastAPI docs) |
| ui | `docker/donna-ui.yml` | 8400 (dashboard) |
| monitoring | `docker/donna-monitoring.yml` | 3000 (Grafana) |
| ollama | `docker/donna-ollama.yml` | 11434 |

## Commands

### Start
Use the existing scripts which handle ordering and flags:
```bash
./scripts/donna-up.sh              # core only
./scripts/donna-up.sh --all        # everything
./scripts/donna-up.sh --with-dashboard --with-monitoring  # selective
```

### Stop
```bash
./scripts/donna-down.sh
```

### Restart a single service
```bash
COMPOSE_PROJECT_NAME=donna docker compose -f docker/donna-app.yml --env-file docker/.env up --build -d
```

### Logs
```bash
COMPOSE_PROJECT_NAME=donna docker compose -f docker/donna-core.yml --env-file docker/.env logs -f --tail=100
```

### Status
```bash
docker ps --filter "label=com.docker.compose.project=donna" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Health check
```bash
curl -s http://localhost:8100/health | python3 -m json.tool
curl -s http://localhost:8200/docs > /dev/null && echo "API: up" || echo "API: down"
curl -s -o /dev/null -w "%{http_code}" http://localhost:8400/
```

## Notes
- Always use `--env-file docker/.env` — secrets are not in the environment
- Always use `COMPOSE_PROJECT_NAME=donna` for direct docker compose commands
- The `--build` flag is needed when code changes affect Dockerfiles
- Never expose `docker/.env` contents
