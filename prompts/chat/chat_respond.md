# Chat Response

Respond to the user's message given the context below.

## Instructions

- Answer based on the provided context data. Do not make up task details, dates, or agent outputs.
- If the user asks about something not in the context, say you don't have that information.
- If a task action is requested, include the action details in `suggested_actions`.
- If the conversation is clearly about a specific task and the session is not pinned, suggest pinning via `pin_suggestion`.
- Set `needs_escalation` to true if you cannot confidently answer — the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment. Include a clear reason in `escalation_reason`.

## Context

{{ system_prompt }}

## Conversation History

{{ conversation_history }}

## User Message

{{ user_input }}
