# Intent Classification

Classify the user's message into exactly one intent category, and optionally suggest which action domain and specific action it maps to.

## Categories

- **task_query**: Asking about tasks — status, list, details, schedule, deadlines
- **task_action**: Requesting a change — create, reschedule, reprioritize, complete, cancel a task
- **agent_output_query**: Asking about what agents did — prep results, research output, agent activity
- **planning**: Asking for planning advice — "what should I focus on?", "am I overcommitted?", workload assessment
- **freeform**: General conversation, not tied to a specific system action or data lookup
- **escalation_request**: User explicitly asks for Claude's help or a more capable model

## Action Domains

If the message maps to a specific action, provide the `domain` and `action_hint`:

- **tasks**: query_tasks, get_task, create_task, update_task, reschedule_task
- **vault**: read_vault_file, create_vault_note, list_vault_files
- **skills**: execute_skill, list_skills, create_skill_draft
- **automations**: create_automation, list_automations
- **debug**: get_debug_data, get_agent_status

## Output

Respond with a JSON object:

```json
{
  "intent": "task_action",
  "domain": "tasks",
  "action_hint": "create_task",
  "needs_escalation": false,
  "escalation_reason": null
}
```

Set `domain` and `action_hint` to null if the message doesn't clearly map to a specific action. Set `needs_escalation` to true ONLY if the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment beyond your capability.

## Current Context

Today's date: {{ current_date }}
User: {{ user_name }}

## User Message

{{ user_input }}
