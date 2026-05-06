# Chat Escalation Prompt

> Realizes `docs/superpowers/specs/manual-escalation.md` §5.2. This file
> is the canonical body of the prompt the user pastes into claude.ai. It
> is rendered into `${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md`
> and mirrored into `escalation_request.prompt_body`.

## Context

- **Correlation ID:** `{{ correlation_id }}`
- **Task type:** `{{ task_type }}`
{%- if task_id %}
- **Task ID:** `{{ task_id }}`
{%- endif %}
- **Estimate (USD):** ${{ "%.2f"|format(estimate_usd) }}
- **Daily budget remaining (USD):** ${{ "%.2f"|format(daily_remaining_usd) }}
- **Iteration:** {{ iteration }}{% if iteration > 1 %} (re-escalation){% endif %}

## Why you're seeing this

Donna estimated this task would exceed today's API budget envelope, so it
parked the work and is asking you to answer it manually. Paste the
question below into the Claude UI of your choice, then bring the answer
back via:

1. The dashboard escalation detail page —
   `/admin/escalations/{{ correlation_id }}` (recommended).
2. Or the Discord slash command —
   `/donna submit {{ correlation_id }} <answer>` (short answers only).

The minimum answer length is **50 characters**. The slash command rejects
payloads over **3000 characters** and tells you to use the dashboard.

## Question

{{ original_prompt }}

## After you answer

Donna will:

- Append your answer to the originating task's notes.
- Mark the task as `done`.
- Mark this escalation as `validated` and write an `escalation_validated`
  audit entry tied to correlation ID `{{ correlation_id }}`.

Iterate via the dashboard re-submit affordance if the first answer
needs work — capped at 3 attempts.
