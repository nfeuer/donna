---
name: docker-rebuild
description: Rebuild and restart specific Donna Docker services after code changes
disable-model-invocation: true
---

# Docker Rebuild

Rebuild specific Donna services after code changes. Use this instead of full stack restart when only certain services need updating.

## Usage

The user will say what they changed. Map it to the right service(s) to rebuild.

## Change-to-Service Mapping

| What changed | Service to rebuild | Command |
|-------------|-------------------|---------|
| `src/donna/api/` | donna-api | `docker compose -f docker/donna-app.yml --env-file docker/.env up --build -d` |
| `src/donna/orchestrator/`, `src/donna/llm/`, `src/donna/tasks/`, most `src/donna/` | donna-orchestrator | `docker compose -f docker/donna-core.yml --env-file docker/.env up --build -d` |
| `donna-ui/src/` | donna-ui | `docker compose -f docker/donna-ui.yml --env-file docker/.env up --build -d` |
| `config/` (YAML) | donna-orchestrator + donna-api | Configs are volume-mounted, so just restart: `docker restart donna-orchestrator donna-api` |
| `prompts/`, `schemas/` | donna-orchestrator | Volume-mounted, restart: `docker restart donna-orchestrator` |
| `docker/Dockerfile.*` | Affected service | Rebuild with `--build` |
| `docker/donna-monitoring.yml` | monitoring stack | `docker compose -f docker/donna-monitoring.yml --env-file docker/.env up -d` |
| `docker/.env` | all services | Full restart via `./scripts/donna-down.sh && ./scripts/donna-up.sh --all` |

## All Commands

Always prefix direct docker compose commands with:
```bash
export COMPOSE_PROJECT_NAME=donna
```

### Rebuild single service
```bash
COMPOSE_PROJECT_NAME=donna docker compose -f docker/<compose-file>.yml --env-file docker/.env up --build -d
```

### Restart without rebuild (config/prompt changes)
```bash
docker restart donna-orchestrator
```

### View build output
```bash
COMPOSE_PROJECT_NAME=donna docker compose -f docker/<compose-file>.yml --env-file docker/.env build --no-cache <service>
```

### Verify after rebuild
```bash
docker ps --filter "label=com.docker.compose.project=donna" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
curl -s http://localhost:8100/health | python3 -m json.tool
```

## Notes
- `config/`, `prompts/`, `schemas/` are volume-mounted — restart is enough, no rebuild needed
- Source code changes (`src/donna/`) require `--build` to bake into the image
- UI changes (`donna-ui/src/`) require `--build` (Vite build runs in Dockerfile)
- Never expose `docker/.env` contents
