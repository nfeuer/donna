# Domain

Per-subsystem specs, split out of
[`spec_v3.md`](../reference-specs/spec-v3.md). Each page here is the
working reference for that subsystem; the canonical spec is always the
ultimate source of truth.

| Subsystem | Spec section |
|---|---|
| [Task System](task-system.md) | `spec_v3.md §5` |
| [Orchestrator](orchestrator.md) | Central dispatch and intent routing |
| [Model Layer](model-layer.md) | `spec_v3.md §4` |
| [LLM Gateway](llm.md) | Priority queue, rate limiting, GPU tracking |
| [Skill System](skill-system.md) | Skill DSL + executor (Phase 1 setup) |
| [Agents](agents.md) | Agent hierarchy, safety, tool progression |
| [Chat](chat.md) | Conversational engine, action handlers, sessions |
| [Replies](replies.md) | Universal reply classification and routing |
| [Scheduling](scheduling.md) | `spec_v3.md §6` |
| [Integrations](integrations.md) | `spec_v3.md §3.2` (hybrid MCP/API) |
| [Memory Vault](memory-vault.md) | Git-backed knowledge store with embeddings |
| [Notifications](notifications.md) | Channels, escalation, conversation context |
| [Preferences](preferences.md) | Correction logging, rule extraction |
| [Cost](cost.md) | Budget enforcement, escalation gate, tool gap surfacing |
| [Collection](collection.md) | LLM payload capture for forensics |
| [Insights](insights.md) | SQL-based analytics for Claude Inspector |
| [Observability](observability.md) | Logging, dashboards, alerting |
| [Resilience](resilience.md) | `spec_v3.md §3.6` retries, circuit breaker, backup |
| [API & Auth](api.md) | REST API, authentication, authorization |
| [Setup](setup.md) | Interactive bootstrapping wizard |
| [Management GUI](management-gui.md) | Admin and inspection surfaces |
