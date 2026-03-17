# Slice 1: Database & Task CRUD

> **Goal:** Stand up the SQLite database with Alembic migrations, implement basic task CRUD operations, and wire up the invocation log table.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/task-system.md` — Task schema, state definitions
- `docs/architecture.md` — SQLite/WAL mode, NVMe layout, schema migration

## What to Build

1. **Generate the initial Alembic migration** from the SQLAlchemy models in `src/donna/tasks/db_models.py`. This creates all tables: `tasks`, `invocation_log`, `correction_log`, `learned_preferences`, `conversation_context`.

2. **Implement a `Database` class** (`src/donna/tasks/database.py`) that:
   - Opens an `aiosqlite` connection with WAL mode enabled
   - Runs `alembic upgrade head` on startup (apply pending migrations)
   - Provides async CRUD methods: `create_task()`, `get_task()`, `update_task()`, `list_tasks()`, `transition_task_state()`
   - `transition_task_state()` uses the `StateMachine` to validate transitions and returns the list of side effects to execute
   - All writes happen inside SQLite transactions (atomicity guarantee)

3. **Implement invocation logging** (`src/donna/logging/invocation_logger.py`):
   - Accepts an `InvocationMetadata` + request context
   - Writes to the `invocation_log` table
   - This will be called by the `ModelRouter` after every model call

4. **Add integration tests** that create an in-memory SQLite DB, run migrations, and test the full CRUD cycle including state transitions.

## Acceptance Criteria

- [ ] `alembic upgrade head` creates all tables in a fresh SQLite database
- [ ] `alembic downgrade -1` successfully rolls back the migration
- [ ] `create_task()` inserts a task and returns it with a generated UUID
- [ ] `get_task()` retrieves a task by ID
- [ ] `list_tasks()` filters by status, domain, user_id
- [ ] `transition_task_state("backlog", "scheduled")` succeeds and returns side effects
- [ ] `transition_task_state("backlog", "done")` raises `InvalidTransitionError`
- [ ] All DB operations are async (`await`)
- [ ] WAL mode is enabled (verify with `PRAGMA journal_mode`)
- [ ] Invocation logger writes to `invocation_log` table
- [ ] Integration tests pass with in-memory SQLite

## Not in Scope

- No Discord, no calendar, no LLM calls
- No Supabase sync (that's a later slice)
- No backup automation

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/task-system.md`, `docs/architecture.md`
