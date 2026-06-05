# Fallback Observability — Design Spec

**Date:** 2026-05-19
**Spec:** `spec_v3.md §5.3 Notifications`, `§3.2 Model Router`, `§10.2 Degraded Mode`

## Problem

Donna has 16 code paths where a primary operation fails and the system silently falls back to a degraded alternative. The user has no visibility into these fallbacks unless they read raw structured logs in Loki.

**Concrete example:** On 2026-05-19, the morning digest routed to the local Ollama model (Qwen 2.5). The model returned `{"title": ..., "description": ..., "color": ...}` instead of the required `{"digest_text": ..., "task_count": ..., "overdue_count": ...}`. Line 130 of `digest.py` did `result.get("digest_text")` → `None`. The code entered degraded mode and posted a plain-text digest to Discord. No exception was thrown (the LLM call succeeded — it just returned the wrong schema), so `logger.exception("morning_digest_llm_failed")` never fired. The fallback was completely invisible.

**Impact:** The user thought the LLM output was just low quality. The bug went undetected because the fallback path worked correctly but silently. Without notification, silent fallbacks become permanent fallbacks that never get fixed.

## Audit Results

| # | File | Lines | Pattern | Has log? | Has notification? |
|---|------|-------|---------|----------|-------------------|
| 1 | `digest.py` | 125–161 | LLM → degraded text | Yes | No |
| 2 | `digest.py` | 183–192 | Calendar API fail → "No events" | Yes | No |
| 3 | `digest.py` | 233–252 | Cost query fail → $0.00 | Yes | No |
| 4 | `digest.py` | 260–278 | Tool gaps query fail → "None." | Yes | No |
| 5 | `digest.py` | 113–121 | Self-diagnostic fail → "All normal" | Yes | No |
| 6 | `digest.py` | 255 | Config read → contextlib.suppress | No | No |
| 7 | `weekly_digest.py` | 84–118 | LLM → fallback stats table | Yes | No |
| 8 | `eod_digest.py` | 358–373 | Cost/skill query fail | Yes | No |
| 9 | `reminders.py` | 148–189 | LLM → template string | Yes | No |
| 10 | `overdue.py` | 196–249 | LLM → template string | Yes | **Yes** |
| 11 | `router.py` | 345–416 | Ollama → Claude fallback | Yes | No |
| 12 | `router.py` | 491–505 | Ollama recovery | Yes (INFO) | No |
| 13 | `auto_scheduler.py` | 58–83 | Calendar → empty slot list | Yes (INFO) | No |
| 14 | `service.py` | 198–203 | DM send fail → returns True | Yes | No |
| 15 | `discord_views.py` | 1122–1223 | contextlib.suppress on edit | No | No |
| 16 | `discord_bot.py` | 882 | contextlib.suppress on JSON parse | No | No |

Only site #10 (`overdue.py`) notifies via `#donna-debug`. All others are invisible without log access.

## Solution

### 1. `dispatch_fallback_alert()` on `NotificationService`

New method on `NotificationService` with built-in rate limiting:

```python
async def dispatch_fallback_alert(
    self,
    component: str,
    error: str,
    fallback: str,
    context: dict[str, Any] | None = None,
    cooldown_seconds: int = 3600,
) -> bool:
```

**Parameters:**
- `component`: identifier for the subsystem (e.g. `"morning_digest"`, `"model_router"`, `"reminder"`)
- `error`: what went wrong — the exception message or a description of the unexpected state
- `fallback`: what the system did instead of the primary path
- `context`: optional structured data (task_id, model alias, expected vs actual keys, etc.)
- `cooldown_seconds`: dedup window — same `(component, error_type)` within this window skips Discord dispatch but still logs at WARNING

**Rate limiting:** In-memory `dict[tuple[str, str], datetime]` on the `NotificationService` instance, keyed on `(component, first_50_chars_of_error)`. If a matching key was dispatched within `cooldown_seconds`, the method logs at WARNING but does not dispatch to Discord. Returns `False` when deduped, `True` when dispatched.

**Message format** posted to `#donna-debug`:

```
⚠️ Fallback activated: {component}
Error: {error}
Fallback: {fallback}
{context formatted as key: value lines, if provided}
```

**Recursion guard:** If dispatching the alert itself fails, log at ERROR level but do not attempt to alert about the alert failure. A simple boolean flag `_alerting` prevents re-entry.

### 2. Morning Digest Immediate Fix

**2a. Prompt template update** (`prompts/morning_digest.md`):

Add the required JSON output format to the prompt so the local Qwen model knows what keys to produce:

```markdown
## Output Format

Return a JSON object with exactly these fields:

    {"digest_text": "<the full digest message, under 2000 chars>", "task_count": <integer>, "overdue_count": <integer>}

Do not use any other keys. The digest_text field contains the complete message.
```

**2b. Fallback key extraction** in `digest.py` `_fire()`:

After `result.get("digest_text")`, if `digest_text` is None but result is a dict, attempt to salvage from `description` (the key Qwen actually used). When salvaging, dispatch a fallback alert so the schema mismatch is visible.

When entering degraded mode (no LLM text at all), dispatch a fallback alert with the reason.

### 3. Codebase-Wide Retrofit

**Sites 1–9 (digests, reminders):** All have `NotificationService` via `self._service`. Add `dispatch_fallback_alert()` calls at each fallback entry point.

**Site 10 (overdue.py):** Migrate from the local `_alert_debug()` to `dispatch_fallback_alert()` for consistency and rate limiting. Remove the `_alert_debug()` method.

**Sites 11–12 (model router):** Router does not have `NotificationService` and should not — it's a lower layer. Add an optional `fallback_alert_fn: Callable[..., Awaitable[bool]] | None` callback parameter to `ModelRouter.__init__()`. The wiring code (`cli_wiring.py`) binds it to `service.dispatch_fallback_alert`. The router calls it when:
- Ollama context overflow triggers Claude fallback
- Ollama recovers after being degraded

**Site 13 (auto_scheduler):** Already has `NotificationService` via `self._notification_service`. Add alert call when calendar is unavailable and fallback slot selection is used.

**Site 14 (service.py DM failure):** Fix the bug where `dispatch_dm()` returns `True` even on send failure. Add WARNING log. Cannot dispatch a fallback alert here (would risk recursion) — structured log only with `event_type="fallback_activated"`.

**Sites 15–16 (discord_views.py, discord_bot.py):** Replace `contextlib.suppress(Exception)` with explicit `try/except` blocks that log at WARNING with `event_type="fallback_activated"`, the component name, and the exception. These are UI-level edge cases without `NotificationService` access — structured logs only, no Discord dispatch.

### 4. Prevention

**4a. CLAUDE.md convention.** Add to the "Conventions" section:

```
- Every try/except that falls back to a default or degraded behavior must call
  `dispatch_fallback_alert()` (or log with `event_type="fallback_activated"` if
  NotificationService is unavailable). Never use `contextlib.suppress(Exception)`.
```

**4b. CI check.** A pytest test that scans all `.py` files under `src/donna/` for `contextlib.suppress(Exception)` and fails if any are found. Targeted at the single worst pattern.

**4c. Method docstring.** `dispatch_fallback_alert()` includes a "When to call" section in its docstring explaining the convention.

## Files Changed

| File | Change |
|------|--------|
| `src/donna/notifications/service.py` | Add `dispatch_fallback_alert()` method with rate limiting |
| `prompts/morning_digest.md` | Add JSON output format section |
| `src/donna/notifications/digest.py` | Fallback key extraction + 6 alert calls |
| `src/donna/notifications/weekly_digest.py` | 1 alert call |
| `src/donna/notifications/eod_digest.py` | 2 alert calls |
| `src/donna/notifications/reminders.py` | 1 alert call |
| `src/donna/notifications/overdue.py` | Migrate `_alert_debug()` → `dispatch_fallback_alert()` |
| `src/donna/models/router.py` | Add `fallback_alert_fn` callback, call on overflow + recovery |
| `src/donna/scheduling/auto_scheduler.py` | 1 alert call |
| `src/donna/notifications/service.py` | Fix DM `return True` bug, add structured log |
| `src/donna/integrations/discord_views.py` | Replace 4 `contextlib.suppress` with try/except + log |
| `src/donna/integrations/discord_bot.py` | Replace 1 `contextlib.suppress` with try/except + log |
| `CLAUDE.md` | Add fallback observability convention |
| `tests/` | CI check for `contextlib.suppress(Exception)`, unit tests for `dispatch_fallback_alert()` |

## Non-Goals

- Full AST linter for fallback patterns — CLAUDE.md convention + targeted check is sufficient at current codebase size
- Loki alert rules — direct Discord dispatch is more reliable and simpler
- Restructuring existing fallback logic — we're adding observability to existing patterns, not redesigning them
- Alerting on expected/normal fallbacks — when a dependency is `None` because it was never configured (e.g., `self._router is None`, `self._calendar_client is None`), that's expected and should not alert. The distinction: if the dependency was configured and then failed at runtime, alert. If it was never wired up, don't.
