# Feature Map

Donna's capabilities at a glance, organized by subsystem with implementation status and navigation links.

| Feature | Status | Domain | Workflow |
|---------|--------|--------|----------|
| **Task capture** — parse natural-language messages into structured tasks | Shipped | [Task System](domain/task-system.md) | [Capture a Task](workflows/capture-a-task.md) |
| **Task deduplication** — fuzzy title match + LLM semantic comparison | Shipped | [Task System](domain/task-system.md) | [Capture a Task](workflows/capture-a-task.md) |
| **Task state machine** — configurable lifecycle from `CAPTURED` through `DONE` | Shipped | [Task System](domain/task-system.md) | |
| **Scheduling** — auto-schedule tasks to calendar slots, conflict resolution | Shipped | [Scheduling](domain/scheduling.md) | |
| **Calendar sync** — bidirectional Google Calendar integration | Shipped | [Scheduling](domain/scheduling.md), [Integrations](domain/integrations.md) | |
| **Reminder cadence** — T-24h, T-1h, T notifications with escalation | Shipped | [Notifications](domain/notifications.md) | |
| **Skill system** — YAML-defined skills with LLM execution, tool dispatch, validation | Shipped | [Skill System](domain/skill-system/index.md) | [Run a Skill](workflows/run-a-skill.md), [Add a New Skill](workflows/add-a-new-skill.md) |
| **Agent hierarchy** — sub-agents with progressive autonomy and tool access | Shipped | [Agents](domain/agents.md) | |
| **Model routing** — alias-based routing across Claude API and Ollama | Shipped | [Model Layer](domain/model-layer.md) | |
| **Budget enforcement** — $20/day soft pause, $100/month hard cap | Shipped | [Cost](domain/cost.md) | [Handle Budget Breach](workflows/handle-budget-breach.md) |
| **Escalation gate** — over-budget decision tree (Approve / Manual / Pause / Cancel) | Shipped | [Cost](domain/cost.md) | |
| **Discord bot** — primary interaction channel, DMs, thread tracking | Shipped | [Integrations](domain/integrations.md) | |
| **SMS/Voice** — Twilio integration for text and voice reminders | Shipped | [Integrations](domain/integrations.md) | |
| **Gmail** — draft-only email integration | Shipped | [Integrations](domain/integrations.md) | |
| **Chat engine** — conversational interface with action handlers and session persistence | Shipped | [Chat](domain/chat.md) | |
| **Memory vault** — Git-backed knowledge store with embeddings and semantic search | Shipped | [Memory Vault](domain/memory-vault/index.md) | |
| **Preference learning** — event-driven correction logging and rule extraction | Shipped | [Preferences](domain/preferences.md) | |
| **Automations** — cron-dispatched recurring skills with cadence policy | Shipped | [Skill System](domain/skill-system/index.md) | |
| **Payload collection** — capture full LLM call payloads for forensics | Shipped | [Collection](domain/collection.md) | |
| **Claude Inspector** — UI for browsing LLM calls, comparing payloads, cost insights | Shipped | [Insights](domain/insights.md), [Management GUI](domain/management-gui/index.md) | |
| **Management dashboard** — React admin UI with task, skill, log, and vault views | Shipped | [Management GUI](domain/management-gui/index.md) | |
| **Observability** — Grafana + Loki dashboards, structured logging | Shipped | [Observability](domain/observability.md) | |
| **Resilience** — retry policies, circuit breaker, health checks | Shipped | [Resilience](domain/resilience.md) | |
| **Supabase sync** — async write-through to cloud Postgres replica | Shipped | [Integrations](domain/integrations.md) | |
| **Setup wizard** — interactive bootstrapping for new instances | Shipped | [Setup](domain/setup.md) | |
| **Alembic migrations** — schema versioning with roundtrip safety | Shipped | | [Add a Migration](workflows/add-a-migration.md) |
| **Flutter app** — Web + Android UI | Planned | | |
| **Multi-user** — second user support | Planned | | |
