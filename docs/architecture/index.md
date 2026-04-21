# Architecture

Donna is a hub-and-spoke system: a central orchestrator routes input from
channels (Discord, SMS, email) through the model layer and skill runtime,
persists state in SQLite, mirrors to Supabase, and emits structured logs to
Grafana/Loki.

| Page | What it covers |
|---|---|
| [Overview](overview.md) | Components, Docker topology, GPU isolation, storage layout |
| [Data Flow](data-flow.md) | How a message becomes a task, a reminder, an action |
| [Component Map](component-map.md) | Every module under `src/donna/` and how they connect |

Authoritative source: [`spec_v3.md` §3 Architecture](../reference-specs/spec-v3.md).
