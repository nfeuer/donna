# Intent Classification

Classify the user's message into exactly one intent category.

## Categories

- **task_query**: Asking about tasks — status, list, details, schedule, deadlines
- **task_action**: Requesting a change — create, reschedule, reprioritize, complete, cancel a task
- **agent_output_query**: Asking about what agents did — prep results, research output, agent activity
- **planning**: Asking for planning advice — "what should I focus on?", "am I overcommitted?", workload assessment
- **freeform**: General conversation, not tied to a specific system action or data lookup
- **escalation_request**: User explicitly asks for Claude's help or a more capable model

## Output

Respond with a JSON object. Set `needs_escalation` to true ONLY if you cannot confidently answer — the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment beyond your capability.

## Current Context

Today's date: {{ current_date }}
User: {{ user_name }}

## User Message

{{ user_input }}
