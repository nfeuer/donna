# Quickstart

Minimal end-to-end loop — clone, configure, run, capture a task, see it
flow through the system.

## 1. Clone and configure

```bash
git clone https://github.com/nfeuer/donna
cd donna
cp docker/.env.example docker/.env
# Edit docker/.env with API keys (Anthropic, Discord, Twilio, Google, …)
```

## 2. Install

```bash
pip install -e ".[dev]"
alembic upgrade head
```

## 3. Run

```bash
# Dev mode (human-readable logs, single process)
donna run --dev

# Or full homelab stack
docker compose -f docker/donna-core.yml up --build
```

## 4. Capture your first task

Send Donna a Discord DM:

> "Remind me to review the quarterly plan tomorrow at 10am. High priority."

Trace what happens in [Workflows → Capture a Task](../workflows/capture-a-task.md).

## 5. Inspect logs

Every LLM invocation is logged — see
[Operations → Budget & Cost](../operations/budget-and-cost.md) and
[Domain → Observability](../domain/observability.md).

## Next Steps

- Read the [Conventions](conventions.md) page before your first change.
- Open the [API Reference](../reference/) and browse
  `donna.skills.executor` — that's the heart of the runtime.
