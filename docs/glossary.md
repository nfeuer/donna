# Glossary

Donna-specific terms and their definitions.

| Term | Definition | See Also |
|------|-----------|----------|
| **Action handler** | A chat engine plugin that intercepts natural-language commands (e.g., "show my tasks") and translates them into API calls. Registered via the `ActionRegistry`. | [Chat](domain/chat.md) |
| **Automation** | A cron-dispatched recurring skill execution with cadence policy and pause/resume lifecycle. | [Skill System](domain/skill-system.md) |
| **BudgetGuard** | The pre-call gate that checks spend against daily ($20) and monthly ($100) thresholds before allowing an LLM invocation. | [Cost](domain/cost.md), [Handle Budget Breach](workflows/handle-budget-breach.md) |
| **Capability** | A unit of agent functionality (e.g., `task_create`, `email_draft`) with defined tool access and autonomy level. | [Agents](domain/agents.md), [Skill System](domain/skill-system.md) |
| **Challenger** | A second model invocation used to verify high-stakes outputs via a compare-and-decide pattern. | [Model Layer](domain/model-layer.md) |
| **Circuit breaker** | A resilience pattern that stops retrying a failing service after consecutive failures, entering an open state until a cooldown expires. | [Resilience](domain/resilience.md) |
| **Collection** | The subsystem that captures full LLM request/response payloads to disk for forensic analysis via Claude Inspector. | [Collection](domain/collection.md) |
| **Correction** | A user-initiated change to a task field (priority, due date, etc.) that the preference system observes and learns from. | [Preferences](domain/preferences.md) |
| **CorrectionSubscriber** | The event-driven listener that detects task field changes and logs them as corrections for preference learning. | [Preferences](domain/preferences.md) |
| **Dedup** | Deduplication of incoming tasks via fuzzy title matching and LLM semantic comparison against existing tasks. | [Task System](domain/task-system.md) |
| **Escalation gate** | The over-budget decision UI (Approve $X / Manual / Pause / Cancel) that replaces the simple pause-only behavior. | [Cost](domain/cost.md) |
| **Evictor** | The `PayloadEvictor` component that enforces disk budget by deleting oldest payload files when storage exceeds thresholds. | [Collection](domain/collection.md) |
| **GPU tracker** | Monitors Ollama GPU memory usage on the RTX 3090 to inform model routing decisions. | [LLM Gateway](domain/llm.md) |
| **Invocation log** | The `invocation_log` SQLite table that records every LLM call with model, latency, tokens, cost, and task context. | [Observability](domain/observability.md), [Model Layer](domain/model-layer.md) |
| **Memory vault** | A Git-backed knowledge store that chunks, embeds, and indexes content from tasks, chat, and corrections for semantic retrieval. | [Memory Vault](domain/memory-vault.md) |
| **Model alias** | A logical name (e.g., `task_parse`, `chat_respond`) mapped to a specific model + parameters in `config/donna_models.yaml`. | [Model Layer](domain/model-layer.md) |
| **ModelRouter** | The central abstraction that routes all LLM calls through alias lookup, budget checking, and structured logging. | [Model Layer](domain/model-layer.md) |
| **Orchestrator** | The central dispatcher that receives user messages, classifies intent, and routes to the appropriate skill or handler. | [Orchestrator](domain/orchestrator.md) |
| **Payload** | The full JSON capture of an LLM request (prompt, parameters) and response (output, tokens, latency) stored by the collection subsystem. | [Collection](domain/collection.md) |
| **Priority engine** | Computes dynamic task priority from urgency, importance, dependencies, and user corrections. | [Scheduling](domain/scheduling.md) |
| **Rule applier** | The preference subsystem component that applies learned rules (from corrections) to new tasks at creation time. | [Preferences](domain/preferences.md) |
| **Shadow preference** | A candidate preference rule extracted from corrections that needs validation before becoming active. | [Preferences](domain/preferences.md) |
| **Skill** | A YAML-defined unit of work with steps, tool access, model routing, and validation. The atomic unit of Donna's capability system. | [Skill System](domain/skill-system.md), [Run a Skill](workflows/run-a-skill.md) |
| **Slice** | A build increment with defined acceptance criteria and spec references, used to scope implementation work. | [Development: Slices](development/slices.md) |
| **State machine** | The configurable task lifecycle defined in `config/task_states.yaml` that governs valid transitions between states. | [Task System](domain/task-system.md) |
| **Supabase sync** | Async write-through replication from local SQLite to a Supabase Postgres instance for cloud access and backup. | [Integrations](domain/integrations.md) |
| **TaskEventBus** | The pub/sub system that broadcasts task lifecycle events (created, updated, completed) to subscribers like `CorrectionSubscriber`. | [Task System](domain/task-system.md), [Preferences](domain/preferences.md) |
| **Tool dispatch** | The validation layer where models propose tool calls and the orchestrator validates and executes them. Models never call tools directly. | [Agents](domain/agents.md), [Skill System](domain/skill-system.md) |
| **WAL mode** | SQLite Write-Ahead Logging mode, enabling concurrent reads during writes. All Donna SQLite databases use WAL. | [Architecture: Overview](architecture/overview.md) |
