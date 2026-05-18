---
name: new-migration
description: Create an Alembic migration with merge-head check and Supabase sync warnings
disable-model-invocation: true
---

# New Migration

Create an Alembic schema migration safely. Donna uses SQLAlchemy models with Alembic for all schema changes — never modify tables manually.

## Workflow

1. **Check for multiple heads first:**
   ```bash
   alembic heads
   ```
   If there are multiple heads, create a merge migration first:
   ```bash
   alembic merge heads -m "merge <branch_a> and <branch_b> heads"
   ```

2. **Generate the migration stub:**
   ```bash
   alembic revision -m "<description>"
   ```

3. **Write the upgrade and downgrade:**
   - Use `op.add_column`, `op.create_table`, etc. — never raw SQL unless required
   - Always write a working `downgrade()` — no `pass` downgrades
   - For `NOT NULL` columns on existing tables, use a server_default or do a three-step: add nullable, backfill, alter to non-null

4. **Test the migration roundtrip:**
   ```bash
   DATABASE_URL=sqlite:///._tmp_migration_check.db alembic upgrade head
   DATABASE_URL=sqlite:///._tmp_migration_check.db alembic downgrade -1
   DATABASE_URL=sqlite:///._tmp_migration_check.db alembic upgrade head
   rm ._tmp_migration_check.db
   ```

5. **Check Supabase sync impact.** If the migration touches any of these tables, warn the user that the Supabase replica schema must be updated separately:
   - `tasks`
   - `task_events`
   - `invocation_log`
   - `escalation_requests`

6. **Verify single head after:**
   ```bash
   alembic heads
   ```

## Conventions
- Migration message format: descriptive snake_case (e.g. `add_chat_action_columns`)
- SQLite WAL mode — avoid operations SQLite doesn't support (e.g. `ALTER COLUMN` type changes require table rebuild)
- Models live in `src/donna/models/` — update the SQLAlchemy model to match the migration
- Run `pytest tests/unit/ -x -q` after to catch model/migration drift
