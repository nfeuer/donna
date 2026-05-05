# Slice 20: Chat Mode

> **Goal:** Wire `chat` mode end-to-end. Builds on slice 17's escalation core and slice 19's dashboard workspace. Adds the local-Ollama summary generator, Discord MD attachment delivery, the `/admin/escalations/<id>/submit` chat-mode payload (textarea), and the `/donna submit` slash-command fallback. Result of a chat mode escalation flows back into the originating task as its `result`.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §5.2 (chat mode protocol — full), §6.1 `prompt_delivery` block, §10.2 (prompt delivery failures — all rows), §10.3 row 1 (empty/malformed answer rejection), §10.10 (`escalation_submitted` audit log).
**Related upstream specs:** `spec_v3.md §13.1` (Budget Rules — chat mode is a manual-handoff terminal), `spec_v3.md §4.3` (invocation_log).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §5.2 — Chat mode protocol

Used for `task_types` whose output is pure text: `chat_escalation`, high-context Q&A, advice, summarization.

Surface split: Discord is the alert; the **admin dashboard is the canonical workspace**. This solves Discord's 2000-char message limit definitively — full prompts can be arbitrarily long — and gives a structured submit path instead of paste-into-thread.

Donna → user:
1. Render the prompt template; store full prompt in `escalation_request.prompt_body` and on disk at `${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md`.
2. Generate a 1–3 sentence summary via local Ollama (no API cost) — title, gist, estimate, daily remaining.
3. Discord notification carries: summary, correlation ID, dashboard link, escalation buttons (§4), optional MD attachment of full prompt.

User → Donna (dashboard primary path):
1. User opens dashboard escalation detail page.
2. Sees prompt + Copy button + textarea + Submit button.
3. Pastes prompt into claude.ai externally, pastes answer back into textarea, clicks Submit.
4. Server validates, writes `escalation_request.result`, marks `status='submitted'`.

User → Donna (Discord fallback):
- `/donna submit <correlation_id>` slash command. Discord slash command arg limit ~6000 chars; rejects payloads near the limit with "use dashboard for long answers".

### §6.1 — `prompt_delivery` YAML block

```yaml
prompt_delivery:
  attach_full_prompt_to_discord: true   # Discord MD attachment alongside summary
  discord_summary_max_chars: 1500       # safety margin under 2000 char message limit
  attachment_size_limit_mb: 25          # Discord free-tier ceiling; MD never approaches this
```

### §10.2 — Prompt delivery failures (mitigations to wire here)

| Failure | Mitigation |
|---|---|
| Rendered prompt > Discord 2000 char limit | Non-issue by design — full prompt is in DB + disk. Discord carries summary + optional attachment only. |
| Discord attachment upload fails (rate limit / network) | Best-effort. Notification still posts with summary + dashboard link. Log `attachment_upload_failed`. |
| Summarizer (local Ollama) is down | Fall back to deterministic templated summary: "{task_type} request — estimate ${estimate}. Click for full prompt." Never blocks the escalation. |
| Dashboard down when user clicks notification link | MD attachment in Discord acts as backup read-only view. Submission still requires dashboard or `/donna submit`. |
| User on mobile with no MD reader | Discord client previews .md as text inline; mobile app handles attachment preview. |

### §10.3 row 1 — Submission validation

| Failure | Mitigation |
|---|---|
| User submits empty / malformed answer in chat mode | `/donna submit` validates non-empty + min length (50 chars default). Discord button click without text reply prompts "paste your answer first". |

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §5.2, §6.1, §10.2
- Slice 17 (escalation core) — depends on it
- Slice 19 (dashboard workspace) — depends on it; this slice attaches the textarea + submit handler
- `prompts/escalation/chat_question.md` — new Jinja template (§9 of canonical spec)
- `src/donna/llm/` — for the local-Ollama summary call (route through `complete()`)
- `src/donna/integrations/discord_views.py` — Discord attachment delivery

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- Spec §12 Q4 — SMS tier-2 fallback rate limits (10/day) when Discord is down for chat-mode escalations. Should chat-mode escalations bypass SMS fallback (text is long, SMS truncated) and rely on the timeout → `paused` path?

## Not in Scope

- `claude_code` mode (slice 21).
- Tool gap surfacing (slice 22).
- Mobile responsive tweaks beyond what slice 19 already shipped.
- Vault-redaction of prompt body — current spec §10.8 accepts raw delivery.

## Session Context

Load only: `CLAUDE.md`, this brief, the canonical spec, slices 17 and 19 outputs, `prompts/escalation/chat_question.md` (new), the existing `discord_views.py`, the LLM gateway (`complete()`).

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Pick the Ollama summary task_type alias and add to `donna_models.yaml` — should it route to the existing `chat_summarizer` alias, or get its own (`escalation_summary`)?
- [ ] What's the contract for `result` ingestion? Does the submitted text become the originating task's `result_payload` directly, or does it pass through a parser/schema check first?
- [ ] How is the originating task informed of the result? Polling pattern from `manual_draft_poller.py`, or event-driven hook?
- [ ] Slash-command argument length cap — confirm Discord's actual limit (6000 was approximate); add server-side rejection for safety.
- [ ] What does the dashboard textarea send on submit — plain text only, or a small JSON envelope with metadata (timestamp, time-to-answer)?
- [ ] Privacy: should we offer a "redact vault references" preview before sending to Discord? Spec §10.8 says no — confirm.
- [ ] Re-escalation (iteration > 1): does the dashboard textarea pre-fill with the prior answer for editing, or stay empty?

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the chat protocol differ from §5.2? Update §5.2.
- [ ] Did the YAML keys differ from §6.1 `prompt_delivery`? Update §6.1.
- [ ] Did the failure mitigations differ from §10.2 / §10.3? Update them.
- [ ] Did the new prompt template / schema land in different paths than §9? Update §9.
- [ ] Did acceptance criteria need adjustment? Update §11.
