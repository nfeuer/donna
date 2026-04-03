# Slice 10: Multi-User Orchestration & REST API

> **Goal:** Expose the orchestrator's internal API as a FastAPI REST backend that the Flutter app can consume. Add Firebase JWT authentication so the API is user-scoped from day one. Complete the multi-user data model by adding `user_id` to the only table that was missing it (`calendar_mirror`).

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/architecture.md` — App Architecture (Phase 4), Auth Flow
- `docs/task-system.md` — Task schema, state machine, user_id scoping
- `docs/model-layer.md` — invocation_log schema, budget thresholds

## What to Build

### 1. FastAPI Application (`src/donna/api/`)

- **`src/donna/api/__init__.py`** — App factory with FastAPI lifespan. Opens the SQLite `Database` connection on startup, closes on shutdown. Registers all routers. Configures CORS.
- **`src/donna/api/auth.py`** — Firebase JWT validation dependency. Fetches Google's public JWKS and caches for 1 hour. Maps Firebase UID → Donna `user_id` via `DONNA_USER_MAP` env var. Dev bypass via `DONNA_AUTH_DISABLED=true`.
- **`src/donna/api/routes/health.py`** — `GET /health` (unauthenticated). Returns service status + uptime.
- **`src/donna/api/routes/tasks.py`** — Task CRUD: `GET /tasks`, `POST /tasks`, `GET /tasks/{id}`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}`. All scoped to `user_id`.
- **`src/donna/api/routes/schedule.py`** — `GET /schedule?days=N`, `GET /schedule/week`. Returns upcoming scheduled tasks sorted by start time.
- **`src/donna/api/routes/agents.py`** — `GET /agents/activity` (recent LLM invocations), `GET /agents/cost` (daily + monthly totals vs budget).

### 2. Multi-User Data Completion

- **`src/donna/tasks/db_models.py`** — Add `user_id: Mapped[str]` to `CalendarMirror`. This was the only table missing it.
- **`alembic/versions/add_calendar_mirror_user_id.py`** — Migration. Adds `user_id` column with `server_default="nick"`, creates index. Includes `downgrade()`.

### 3. Docker

- **`docker/Dockerfile.api`** — Minimal Python 3.12-slim image. Runs `uvicorn donna.api:app --port 8200`. Non-root `donna` user. Referenced by `docker/donna-app.yml`.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FIREBASE_PROJECT_ID` | `""` | Firebase project ID (JWT audience). Empty = audience check skipped. |
| `DONNA_USER_MAP` | `""` | Comma-separated `firebase_uid:donna_user_id` pairs. |
| `DONNA_DEFAULT_USER_ID` | `"nick"` | Fallback user_id when no map entry matches. |
| `DONNA_AUTH_DISABLED` | `"false"` | Set `"true"` to skip JWT validation in local dev. |
| `DONNA_DB_PATH` | `/donna/db/donna_tasks.db` | Path to the SQLite task database. |
| `DONNA_CONFIG_DIR` | `config/` | Path to YAML config directory. |
| `DONNA_CORS_ORIGINS` | `"*"` | Comma-separated allowed CORS origins. |

## Auth Flow (per `docs/architecture.md`)

1. Flutter authenticates user via Firebase Auth → receives JWT.
2. Flutter sends `Authorization: Bearer <jwt>` with every request.
3. `auth.py` validates JWT against Google's JWKS (RS256, cached 1 hour).
4. `auth.py` maps Firebase UID → Donna `user_id`.
5. All DB queries filter by that `user_id`.

## Acceptance Criteria

- [ ] `uvicorn donna.api:app --port 8200` starts without errors
- [ ] `curl http://localhost:8200/health` returns `{"status": "healthy", ...}`
- [ ] `GET /tasks` returns 401 when no token is provided (auth enabled)
- [ ] `GET /tasks` returns 200 with user's tasks when `DONNA_AUTH_DISABLED=true`
- [ ] `POST /tasks` creates a task owned by the requesting user
- [ ] `GET /tasks/{id}` returns 404 for tasks owned by a different user
- [ ] `DELETE /tasks/{id}` sets status to `cancelled`
- [ ] `GET /schedule/week` returns scheduled tasks sorted by start time
- [ ] `GET /agents/cost` returns daily and monthly totals with budget remaining
- [ ] `alembic upgrade head` applies `add_calendar_mirror_user_id` without error
- [ ] `calendar_mirror` table has `user_id` column with index after migration
- [ ] `docker build -f docker/Dockerfile.api .` succeeds
- [ ] `pytest tests/unit/` still passes (no regressions)

## Not in Scope

- Flutter UI (slice 11)
- Supabase write-through sync service (data already in SQLite; Flutter reads Supabase replica)
- Agent dispatch via the REST API (Flutter reads task state; agents are triggered internally)
- WebSocket / real-time push (Phase 4 push is via FCM, not WebSocket)

## Session Context

Load: `CLAUDE.md`, this slice, `docs/architecture.md`, `docs/task-system.md`, `docs/model-layer.md`
