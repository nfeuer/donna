# Workflow: Add a Migration

**Realizes:** [`spec_v3.md` §3.8 Schema Migration](../reference-specs/spec-v3.md).

**Rule:** Schema changes require an Alembic migration. Never modify tables
manually.

## Steps

1. **Update the SQLAlchemy model** under `src/donna/tasks/` (or the
   relevant subsystem).
2. **Generate the migration.**
    ```bash
    alembic revision --autogenerate -m "add <column> to <table>"
    ```
3. **Review the generated file** in `alembic/versions/`. Autogenerate is
   not perfect — confirm it matches intent and that `downgrade()` is
   correct.
4. **Ensure multi-user.** Every new table needs `user_id` (even if
   deployment is single-user today). See the precedent in
   `alembic/versions/add_calendar_mirror_user_id.py`.
5. **Apply locally.**
    ```bash
    alembic upgrade head
    ```
6. **Test the round-trip.**
    ```bash
    alembic downgrade -1
    alembic upgrade head
    ```
7. **Write data migration if needed.** If rows need backfill, add that
   logic in the same revision — don't split it.
8. **Mirror to Supabase.** The write-through sync layer must know the
   shape; update
   [`donna.integrations.supabase_sync`](../reference/donna/integrations/supabase_sync.md)
   if necessary.

## Danger Zones

- **Never** edit a landed migration — create a new one.
- Always define `downgrade()`; we test recovery.
- If the column is `NOT NULL`, add it nullable first, backfill, then
  tighten.

## Related

- [Operations → Migrations](../operations/migrations.md)
- [Domain → Task System](../domain/task-system.md)
