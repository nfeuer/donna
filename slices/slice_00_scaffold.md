# Slice 0: Project Scaffold & Health Endpoint

> **Goal:** Get the project running as a Docker container with structured logging and a working health endpoint. Validate the development workflow before any complex logic.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/architecture.md` — Docker compose, component map
- `docs/observability.md` — Logging setup, structlog config

## What to Build

1. **Verify `pyproject.toml` installs cleanly.** Run `pip install -e ".[dev]"` and resolve any dependency conflicts.

2. **Wire up `donna run` CLI command** so it:
   - Calls `setup_logging()` from `src/donna/logging/setup.py`
   - Starts an `aiohttp` server on port 8100
   - Exposes a `GET /health` endpoint that returns `{"status": "healthy", "service": "donna-orchestrator", "timestamp": "<ISO8601>"}`
   - Logs startup and shutdown events via structlog

3. **Build and run the Docker container** using `docker/Dockerfile.orchestrator` and `docker/donna-core.yml`. Verify the health endpoint responds from inside the container.

4. **Verify structured logging works.** Start the service in dev mode (`donna run --dev`) and confirm logs are human-readable. Start without `--dev` and confirm logs are JSON. Verify `correlation_id` and other context vars appear in log output.

5. **Run `pytest`** and confirm the existing unit tests pass (state machine, config loader, resilience).

## Acceptance Criteria

- [ ] `pip install -e ".[dev]"` succeeds without errors
- [ ] `donna run --dev` starts the aiohttp server and logs a startup message
- [ ] `curl http://localhost:8100/health` returns 200 with valid JSON
- [ ] `donna run` (without --dev) outputs JSON-formatted structlog entries to stdout
- [ ] `docker compose -f docker/donna-core.yml up --build` starts the container
- [ ] Docker healthcheck passes (container shows as healthy)
- [ ] `pytest tests/unit/` passes all existing tests
- [ ] No hardcoded values — config paths, ports, log levels come from args or env vars

## Not in Scope

- No database, no Discord, no calendar, no LLM calls
- No Grafana/Loki setup (that's a separate slice)
- No real business logic — this is pure infrastructure proof of life

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/architecture.md`, `docs/observability.md`
