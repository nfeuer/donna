# Migrations

Alembic is the **only** way to change the schema. Design reference:
[`spec_v3.md` §3.8 Schema Migration](../reference-specs/spec-v3.md).

## Layout

```
alembic/
  env.py
  script.py.mako
  versions/      # each file is one revision
```

## Common Operations

```bash
# Apply everything
alembic upgrade head

# Roll back one
alembic downgrade -1

# Generate from model changes
alembic revision --autogenerate -m "descriptive message"

# Show current revision
alembic current

# Show history
alembic history --verbose
```

## Authoring

See [Workflow → Add a Migration](../workflows/add-a-migration.md) for
the full recipe.

## Recovery

If a migration fails mid-flight:

1. Inspect `alembic current`.
2. Fix the model / migration file.
3. `alembic downgrade -1` to the known-good state.
4. Re-run `alembic upgrade head`.

Full disaster recovery: [Operations → Backup & Recovery](backup-recovery.md).

## Notable Migrations

| Revision | Description |
|----------|-------------|
| `e3f4a5b6c7d8` | Make `immich_user_id` and `email` nullable on `users` table. Uses `batch_alter_table` (SQLite lacks `ALTER COLUMN`). Enables Discord-onboarded users to exist without Immich accounts. |
