# Workflow: Capture a Task

**Realizes:** [`spec_v3.md` §1.1 Active Task Capture](../reference-specs/spec-v3.md),
[`§5.5.1 Natural Language Task Parsing`](../reference-specs/spec-v3.md).

## Scenario

Nick sends a Discord DM to Donna:

> "Remind me to review the quarterly plan tomorrow at 10am, high priority."

Donna parses the message, classifies priority, deduplicates against
existing tasks, persists the row, schedules reminders, and replies.

## Path Through the Code

1. **Inbound.**
   [`donna.integrations.discord_bot`](../reference/donna/integrations/discord_bot.md)
   receives `on_message` and forwards `(text, user_id, channel_id)` to the
   orchestrator.
2. **Orchestrator intent routing.**
   [`donna.orchestrator`](../reference/donna/orchestrator/index.md)
   identifies this as a *task-capture* intent and invokes the
   `parse_task` skill.
3. **Skill execution.**
   [`donna.skills.executor.SkillExecutor`](../reference/donna/skills/executor.md)
   runs the YAML-defined `parse_task` skill step-by-step.
4. **Model call.**
   [`donna.models.router.ModelRouter.complete`](../reference/donna/models/router.md)
   routes to the configured model for `task_parse`
   (see [`config/donna_models.yaml`](../config/donna_models.md)) and logs
   an invocation row via
   [`donna.logging.invocation_logger`](../reference/donna/logging/invocation_logger.md).
5. **Schema validation.** The structured output is validated against
   [`schemas/task_parse_output.json`](../schemas/task_parse_output.md).
6. **Dedup.**
   [`donna.skills`](../reference/donna/skills/index.md) runs the
   `dedup_check` skill (`spec_v3.md §5.3` — fuzzy title match + LLM
   semantic comparison).
7. **State machine.**
   [`donna.tasks.state_machine`](../reference/donna/tasks/index.md)
   transitions the new task to `SCHEDULED` per
   [`config/task_states.yaml`](../config/task_states.md).
8. **Persist.** [`donna.tasks.database`](../reference/donna/tasks/index.md)
   writes the row; [`donna.integrations.supabase_sync`](../reference/donna/integrations/supabase_sync.md)
   mirrors it.
9. **Schedule.**
   [`donna.scheduling`](../reference/donna/scheduling/index.md) enqueues
   reminder cadence (T-24h, T-1h, T).
10. **Reply.** The Discord bot confirms back to the user.

## Sequence

```mermaid
sequenceDiagram
    participant U as User
    participant D as discord_bot
    participant O as orchestrator
    participant S as SkillExecutor
    participant R as ModelRouter
    participant V as schema validator
    participant DB as tasks.database
    participant SC as scheduling

    U->>D: DM text
    D->>O: route(text, user_id)
    O->>S: run(parse_task, text)
    S->>R: complete(prompt, "task_parse", user_id)
    R-->>S: structured JSON
    S->>V: validate(task_parse_output)
    V-->>S: ok
    S->>S: run(dedup_check, parsed)
    S-->>O: Task
    O->>DB: INSERT tasks WHERE user_id=?
    O->>SC: enqueue reminders
    O-->>D: confirmation text
    D-->>U: reply
```

## Observability

Every hop emits a structured log line with `correlation_id`, `user_id`,
`task_id`. See [Domain → Observability](../domain/observability.md).

## Related

- [Workflow: Run a Skill](run-a-skill.md)
- [Domain: Task System](../domain/task-system.md)
