# Automation Alert Pipeline Improvements — Design Spec

**Date:** 2026-05-14
**Spec:** `spec_v3.md §4.6 Alert DSL`, `§4.2 Challenger`, `§5.3 Notifications`

## Problem

Three gaps in the automation alert pipeline prevent automations from reliably alerting the user:

1. **LLM can't see output fields.** The challenger prompt renders input schemas but not output schemas, so the LLM has no idea what fields exist to write alert conditions against. Result: `alert_conditions: null` most of the time.

2. **No notification channel extraction.** When the user says "text me" or "DM me", the LLM has nowhere to put that preference — the challenger parse schema has no `notification_channels` field.

3. **Alert routing ignores user preference.** `AutomationCreationPath.approve()` hardcodes `alert_channels=["discord_dm"]` regardless of what the user asked for. `AutomationDispatcher` hardcodes `channel=CHANNEL_TASKS` (a channel post, not even a DM) regardless of what `alert_channels` says.

**Net effect:** Even when a product_watch automation fires `triggers_alert=true`, the notification goes to the tasks channel as a broadcast instead of as a DM or SMS to the user.

## Solution

### 1. Add `output_schema_summary` and `default_alert_conditions` to capabilities.yaml

Each on-schedule capability gets two new fields:

```yaml
- name: product_watch
  # ... existing fields ...
  output_schema_summary: |
    ok (bool), price_usd (number|null), currency (string|null),
    in_stock (bool), size_available (bool), triggers_alert (bool), title (string)
  default_alert_conditions:
    field: triggers_alert
    op: "=="
    value: true
```

- `output_schema_summary`: A compact, human-readable summary of the skill output fields. Rendered into the challenger prompt so the LLM can reason about which fields to alert on.
- `default_alert_conditions`: The alert DSL expression that fires when the LLM doesn't provide one. For product_watch, this is `triggers_alert == true`. For news_check/email_triage, same — the skill already computes `triggers_alert` internally.

### 2. Add `notification_channels` to challenger parse output

New field in `schemas/challenger_parse.json`:

```json
"notification_channels": {
  "type": ["array", "null"],
  "items": {"enum": ["discord_dm", "sms", "email", "discord_channel"]},
  "description": "User's preferred alert delivery channels, extracted from phrases like 'text me', 'DM me', 'email me'. Null = use default (discord_dm)."
}
```

Prompt addition in `prompts/challenger_parse.md`:

```
- `notification_channels`: array of preferred delivery channels.
  Extract from phrases like "text me" → ["sms"], "DM me" → ["discord_dm"],
  "email me" → ["email"]. Multiple channels allowed. Null = default (discord_dm).
```

### 3. Render output schema summary in challenger prompt

Add to the capability loop in `prompts/challenger_parse.md`:

```jinja2
{% if cap.output_schema_summary %}
  Output fields: {{ cap.output_schema_summary }}
{% endif %}
```

This gives the LLM the context it needs to write informed alert conditions.

### 4. Fix `AutomationCreationPath.approve()`

Two changes:

**a) Merge alert_conditions with defaults:**
```python
alert_conditions = draft.alert_conditions
if not alert_conditions and default_alert_conditions:
    alert_conditions = default_alert_conditions
```

This requires a new lookup callback (`capability_default_alerts_lookup`) to fetch `default_alert_conditions` from the capability config by name.

**b) Populate alert_channels from draft:**
```python
alert_channels = draft.notification_channels or ["discord_dm"]
```

This requires adding `notification_channels` to the `DraftAutomation` dataclass.

### 5. Fix `AutomationDispatcher` to honor `alert_channels`

Replace the hardcoded `dispatch(channel=CHANNEL_TASKS)` with a loop over `automation.alert_channels`:

```python
for channel in (automation.alert_channels or ["discord_dm"]):
    if channel == "discord_dm":
        await self._notifier.dispatch_dm(...)
    elif channel == "sms":
        await self._notifier.dispatch_sms(...)
    elif channel == "email":
        await self._notifier.dispatch_email(...)
    elif channel == "discord_channel":
        await self._notifier.dispatch(channel=CHANNEL_TASKS, ...)
```

For SMS, the recipient is `DONNA_USER_PHONE` from environment (single-user system). For email, it's the user's configured email. For DM, it's `automation.user_id` (Discord snowflake).

## Files Changed

| File | Change |
|------|--------|
| `config/capabilities.yaml` | Add `output_schema_summary` and `default_alert_conditions` to product_watch, news_check, email_triage |
| `schemas/challenger_parse.json` | Add `notification_channels` field |
| `prompts/challenger_parse.md` | Add output field rendering, notification_channels instructions |
| `src/donna/orchestrator/discord_intent_dispatcher.py` | Add `notification_channels` to `DraftAutomation` dataclass, populate from challenger result |
| `src/donna/automations/creation_flow.py` | Merge alert defaults, populate alert_channels from draft |
| `src/donna/automations/dispatcher.py` | Multi-channel alert dispatch loop |
| `src/donna/cli_wiring.py` | Wire new lookup callback |
| Tests for each changed module |

## Non-Goals

- Pre-filtering capabilities to reduce token count (deferred, monitor first)
- New notification channel types beyond the four existing ones
- Per-automation rate limiting on SMS (handled by existing rate_limit config in sms.yaml)
- Changing the alert DSL itself

## Token Impact

Adding `output_schema_summary` (~50 tokens per capability × 3 on-schedule capabilities) + `notification_channels` instructions (~40 tokens) ≈ 190 additional prompt tokens. At current 3 capabilities, this is well under 5% of context.
