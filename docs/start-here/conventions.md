# Conventions

These rules are non-negotiable. They come from
[`CLAUDE.md`](https://github.com/nfeuer/donna/blob/main/CLAUDE.md) and
[`spec_v3.md` §1.3 Key Design Principles](../reference-specs/spec-v3.md).

## Design Principles

1. **Config over code.** Model routing, task types, state transitions,
   prompt templates, and preferences live as YAML/JSON under
   [`config/`](../config/) and [`schemas/`](../schemas/). Never hardcode
   these in application logic.
2. **Safety first, dial back later.** Agents start with minimal autonomy.
   Email is draft-only. Code goes to feature branches only. Constraints
   relax explicitly via config, never implicitly.
3. **Structured logging on every model call.** Every LLM invocation logs
   `task_type`, `model`, `latency`, `tokens`, `cost`, and the output
   (see [`donna.logging.invocation_logger`](../reference/donna/logging/invocation_logger.md)).
4. **Internal API over MCP for orchestrator calls.** The orchestrator
   calls integrations via direct Python modules. MCP is only for
   LLM-facing dynamic tool discovery.
5. **Model abstraction.** All LLM calls go through
   `router.complete(prompt, task_type, user_id)` — see
   [`donna.models.router`](../reference/donna/models/router.md). Never
   call a provider directly.
6. **Tool validation layer.** Models propose tool calls; the orchestrator
   validates and executes. Models never call tools directly.

## Coding Conventions

- **Async everywhere.** Use `async def` / `await` for all I/O.
- **Type hints on every function signature.** `mypy --strict` is enforced
  in CI.
- **Structured logging via `structlog`.** Never use `print()`.
- **SQLite access via `aiosqlite`.** Single connection, WAL mode.
- **State transitions go through the state machine.** Loaded from
  [`config/task_states.yaml`](../config/task_states.md).
- **Schema changes require an Alembic migration.** Never modify tables
  manually.

## How to Update Docs

- Narrative pages (`architecture/`, `domain/`, `workflows/`,
  `operations/`) are hand-written markdown.
- [API Reference](../reference/), [Config](../config/), and
  [Schemas](../schemas/) pages are **auto-generated** from source by
  [`scripts/gen_ref_pages.py`](https://github.com/nfeuer/donna/blob/main/scripts/gen_ref_pages.py)
  — do not edit the generated pages.
- Keep docstrings in Google style; mkdocstrings renders them.
- When you add a new module under `src/donna/`, it appears in the
  reference automatically on the next `mkdocs build`.
- For any **design decision**, cross-reference the relevant section of
  [`spec_v3.md`](../reference-specs/spec-v3.md).
