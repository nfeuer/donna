# Design: User-Facing Output Standard

**Date:** 2026-07-10 · **Status:** PROPOSED — awaiting Nick's approval, not yet implemented
**Spec anchors:** spec_v3.md §25 (automations subsystem), §7.3 (safety constraints), design principle 1 (config over code)

## Problem

Automation alerts reach Discord as raw JSON. The entire alert path converges on
one function — `AutomationDispatcher._render_alert_content()`
(`src/donna/automations/dispatcher.py:480-484`) — which does
`f"Output: {json.dumps(output, indent=2)}"`. Meanwhile three other surfaces
already format properly and inconsistently with each other: the morning digest
(Jinja2 → LLM → `discord.Embed`), reminders (LLM text with template fallback),
and proactive prompts (hand-built embeds). There is no shared standard.

## Goals

1. No user-visible raw JSON, ever.
2. One rendering seam all surfaces converge on, so tone and shape are consistent.
3. Deterministic first: a template always produces a correct message even when
   every LLM is down. LLM voice is garnish, never load-bearing.
4. Config over code: adding/retuning a format is a YAML + template edit.

## Design

### New module: `src/donna/notifications/output_renderer.py`

```python
@dataclass(frozen=True)
class RenderedMessage:
    text: str                      # plain text — SMS/email/log fallback
    embed: discord.Embed | None    # rich shape for Discord surfaces

class OutputRenderer:
    async def render(self, surface: str, payload: dict[str, Any],
                     context: dict[str, Any] | None = None) -> RenderedMessage: ...
```

`surface` is a key like `automation_alert.product_watch`,
`automation_alert.default`, `reminder.overdue`, `digest.morning`.

### New config: `config/output_formats.yaml`

```yaml
formats:
  automation_alert.product_watch:
    template: prompts/output/product_watch_alert.md.j2
    embed:
      title: "🛍️ {title} — ${price_usd}"
      colour: good_news            # semantic colours: good_news, action_needed, failure, info
      fields: [price_usd, in_stock, size_available]
      url_field: url
    voice_pass: true               # optional local-LLM one-liner, config-gated
  automation_alert.default:        # schema-driven generic: renders key: value lines
    template: prompts/output/generic_alert.md.j2
    embed: {title: "🔔 {automation_name}", colour: info}
```

Resolution order: exact surface key → `automation_alert.default` → plain-text
key/value rendering (never JSON). Unknown fields in a template render as
omitted, not as errors.

### Voice pass (optional, default on for alerts)

After the template renders the facts, one `local_parser` call
(task type `format_user_output`, ~150 tokens) rewrites the *description
sentence only* in Donna's voice — e.g. "That's $4 under your threshold and
size L is in stock — want me to stop watching?". Failure or timeout →
template output ships as-is with `event_type="fallback_activated"` logged
(no alert spam; the message still went out). Facts (numbers, fields, links)
come only from the template, so the LLM cannot corrupt them.

### Integration points

1. `AutomationDispatcher._render_alert_content()` → delegates to
   `OutputRenderer.render(f"automation_alert.{capability_name}", output)`;
   the dispatch path passes `RenderedMessage.embed` through
   `NotificationService` (embed plumbing already exists for the digest).
2. Reminders and proactive prompts migrate onto the renderer in a follow-up
   slice — no behaviour change in this slice, just the automation alerts.
3. Alert truncation (1900-char Discord limit) moves inside the renderer.

### Example: the shirt alert, before and after

Before:
```
Automation 'shirt on sale' alert:
Output: {"price_usd": 29.99, "currency": "USD", "in_stock": true, ...}
```

After (embed):
> **🛍️ Seraphina Gown — $29.99**  *(colour: green)*
> In stock in size L, $4 under your $34 threshold. Want me to keep watching or stop?
> `Price $29.99 · In stock ✓ · Size L ✓` · [View product](…)

## Alternatives considered

- **LLM-renders-everything** (digest pattern for all alerts): rejected as
  load-bearing LLM — an Ollama outage would garble or delay alerts, and it
  costs a model call per alert for mostly-static content.
- **Hardcoded per-capability Python formatters:** rejected — violates
  config-over-code; every new automation type would need a code PR.

## Testing

- Unit: renderer resolution order, missing-field tolerance, truncation,
  voice-pass fallback (LLM raising → template text ships).
- Fixture: golden-file test per configured format against a sample payload.
- Config drift test (existing pattern): every `formats:` key's template file
  exists; every capability in `capabilities.yaml` resolves to a format.

## Rollout

Slice 1: renderer + config + product_watch/news_check/default formats + wire
into dispatcher. Slice 2: reminders + proactive prompts migrate. Slice 3:
digest consolidates. Each slice updates spec_v3.md §25.
