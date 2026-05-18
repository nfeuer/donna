---
name: donna-conventions
description: Project coding conventions for Donna — async patterns, logging, config, state machine, testing
user-invocable: false
---

# Donna Coding Conventions

Follow these conventions when writing or modifying Donna code.

## Async
- All I/O functions use `async def` and `await`. No exceptions.
- SQLite access via `aiosqlite` — single connection, WAL mode.
- Use `asyncio.gather()` for concurrent independent operations.

## Logging
- Use `structlog` exclusively. Never use `print()` or stdlib `logging`.
- Bind context early: `log = structlog.get_logger().bind(task_id=task_id, module="tasks")`
- Every LLM call logs: `task_type`, `model`, `latency_ms`, `input_tokens`, `output_tokens`, `cost_usd`.

## Config
- Model routing, task types, state transitions, preferences — all YAML/JSON in `config/`.
- Never hardcode values that belong in config. If you're writing a string that could change per-deployment, it goes in config.
- Load config via `src/donna/config.py` — never read YAML files directly in application code.

## LLM Calls
- All calls go through `complete(prompt, schema, model_alias)`. Never call `anthropic.Client` or Ollama directly.
- Model aliases are defined in `config/donna_models.yaml`.
- Tool calls: models propose, orchestrator validates and executes. Models never call tools directly.

## State Machine
- All task state transitions go through the state machine loaded from `config/task_states.yaml`.
- Never set `task.status = "done"` directly — use the state machine transition function.
- Valid transitions are defined in config, not in code.

## Schema Changes
- Every schema change requires an Alembic migration. Never modify tables manually.
- SQLAlchemy models in `src/donna/models/` must match the migration.
- SQLite limitations: no `ALTER COLUMN` type changes (use table rebuild pattern).

## Type Hints
- Type hints on all function signatures. No exceptions.
- Use `from __future__ import annotations` for forward references.
- Pydantic models for config and API request/response types.

## Testing
- pytest with `pytest-asyncio` for async tests.
- Unit tests: `tests/unit/` — fast, no external dependencies.
- Integration tests: `tests/integration/` — may use database, mark with `@pytest.mark.integration`.
- Test fixtures in `tests/conftest.py` and `tests/fixtures/`.

## Docstrings
- Google style (rendered by mkdocstrings).
- New modules must have a module-level docstring.
- New public functions/classes need at least `Args`, `Returns`, `Raises`.

## Imports
- Standard library, then third-party, then local (`src/donna/`).
- Prefer explicit imports over star imports.

## Error Handling
- Use structured exceptions, not bare `Exception`.
- Log errors with `log.error()` and include context (task_id, model, etc.).
- Tenacity for retries on external calls (LLM, APIs) — configure in the caller, not the library wrapper.
