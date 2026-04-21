# Contributing

## Before You Start

1. Read [`CLAUDE.md`](https://github.com/nfeuer/donna/blob/main/CLAUDE.md).
2. Read the relevant [Domain](../domain/index.md) page and the related
   section of [`spec_v3.md`](../reference-specs/spec-v3.md).
3. Find the slice in [Development → Slices](slices.md) your change
   belongs to.
4. Run `pytest tests/unit/` before changes.

## Rules

- Follow the [Conventions](../start-here/conventions.md) page without
  exception — config over code, structured logging, model abstraction,
  tool validation.
- Every schema change → an Alembic migration
  ([Workflow](../workflows/add-a-migration.md)).
- Every new skill → YAML + schema + fixtures + tests
  ([Workflow](../workflows/add-a-new-skill.md)).
- Every new module → a docstring. The
  [API Reference](../reference/) renders it automatically.

## Docstring Style

Google style. `mkdocstrings` renders them. Minimum viable docstring:

```python
async def complete(
    prompt: str,
    task_type: str,
    user_id: int,
) -> Completion:
    """Route a completion through the configured model for this task type.

    Args:
        prompt: Fully-rendered prompt string.
        task_type: Alias used to look up the model in
            :file:`config/donna_models.yaml`.
        user_id: Owner of the invocation (logged).

    Returns:
        The structured completion, schema-validated.

    Raises:
        BudgetExceeded: If the daily or monthly cap is tripped.
    """
```

## CI Gates

- `ruff check`
- `mypy --strict`
- `pytest -m "not slow and not llm"`
- `mkdocs build --strict` (docs workflow)

## Branching

- Default branch: `main`.
- Claude sessions: `claude/*`.
- Feature work: `feat/<slice>-<short-desc>`.
