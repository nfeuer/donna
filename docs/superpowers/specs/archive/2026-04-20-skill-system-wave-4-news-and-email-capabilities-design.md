# Skill System Wave 4 — News + Email Capabilities

**Status:** Draft
**Author:** Nick (with brainstorming assistance from Claude)
**Date:** 2026-04-20
**Scope:** Medium. Two new capabilities end-to-end, three new skill-system tools, one cross-capability integration test, one followups-doc cleanup. Estimated 4–6 focused days.
**Predecessors:**
- `2026-04-17-skill-system-wave-3-discord-nl-automation-design.md` (Wave 3 — NL automation + cadence policy).
- `2026-04-17-skill-system-wave-2-first-capability-design.md` (Wave 2 — `product_watch` seed capability).
- `2026-04-16-skill-system-wave-1-production-enablement-design.md` (Wave 1 — production enablement).
- `2026-04-15-skill-system-and-challenger-refactor-design.md` (original skill system + challenger refactor).

---

## 1. Overview

Wave 2 proved the skill system could run one real capability (`product_watch`) end-to-end at `shadow_primary`. Wave 3 proved Discord NL creation could land automations without `POST /admin/automations`. Wave 4 **replicates the Wave 2 capability-seeding pattern twice** — adding `news_check` and `email_triage` — to validate that the framework generalizes beyond a single point-check skill.

What changes conceptually:

1. **Two new capabilities seeded.** `news_check` (RSS/Atom monitoring for topic-matching items) and `email_triage` (Gmail scan for action-required emails from a sender allow-list). Both ship at lifecycle state `sandbox` per the Wave 2 pattern — Claude runs them via `claude_native` until 20 schema-valid runs promote them to `shadow_primary`, at which point `SkillExecutor` fires in parallel with Claude shadow.

2. **Since-last-run semantics.** Dispatcher injects `prior_run_end: ISO-8601 | null` into skill inputs at dispatch time. First-ever run → `null` (catch-up semantics); subsequent runs → the most recent successful `automation_run.end_time` for the same automation. The skill filters by this marker using tool-native facilities (`after:<ts>` for Gmail, `published > ts` for RSS). **No new schema column.** This is the key architectural primitive Wave 4 adds; the pattern future multi-hit capabilities will inherit.

3. **Three new skill-system tools.** `rss_fetch(url, since?)` returns parsed RSS/Atom items. `gmail_search(query, max_results)` returns Gmail message summaries. `gmail_get_message(id)` returns the full body on demand. All thin I/O shims around existing code (`src/donna/integrations/gmail.py` already exists; `feedparser` library for RSS). Read-only enforcement at the wrapper boundary — never construct draft/send calls. All registered into `DEFAULT_TOOL_REGISTRY` at startup. All mockable via `tool_mocks` in fixtures.

4. **Digest-shape alert output across both capabilities.** Same schema as `product_watch`: `{ ok, triggers_alert, message, meta }`. Skill pre-renders the DM text; `NotificationService` treats these runs identically to existing capabilities. One DM per run maximum. No downstream changes. Wave 4 codifies this as the default shape for multi-hit capabilities.

5. **Capability-availability guard at approval time.** `AutomationCreationPath` checks that all tools referenced by a matched capability are registered before writing the automation row. Prevents the "approved-but-unrunnable" class of errors when a dependency (Gmail OAuth, etc.) isn't wired at the current runtime.

6. **Followups-doc rollup.** All Wave-3 P2 and P3 followups (F-W3-A/B/C/D/F/G/H/I/J/K) shipped in commits `50794a1` + `9ae2b8d` on 2026-04-17, but the followups inventory was never updated. Wave 4 closes this doc-drift.

After Wave 4, Nick can DM *"keep an eye on TechCrunch's AI feed every 12h"* or *"tell me about action-required emails from my boss twice a day"* and the skill pipeline runs end-to-end with since-last-run semantics, no schema changes, and no Wave 2-style bespoke wiring.

Wave 4 is deliberately **additive replication** — no new services, no new process boundaries, no schema changes. If we found ourselves needing schema changes to add a second or third capability, that would be a signal the Wave 2 framework doesn't generalize, and we'd pause to fix the framework before adding more capabilities. Instead we prove it does.

---

## 2. Out of Scope

| # | What | Why deferred | When to reconsider |
|---|---|---|---|
| OOS-W4-1 | Dashboard UI for skills + automations (F-4) | Separate brainstorm track; UI approach decision (new SPA vs. extend donna-ui). | Wave 5+ as its own design session. |
| OOS-W4-2 | NL auto-modify existing automations (OOS-W3-8) | *"Bump the jacket check to hourly"* → matching phrase to `automation_id` is its own inference problem. | Wave 5+ with F-4 as interim UI. |
| OOS-W4-3 | Event triggers / `on_event` subsystem (OOS-1) | No concrete webhook use case yet; polling covers everything today. | When a push-only source (e.g., Stripe webhook) appears. |
| OOS-W4-4 | `meeting_prep` capability | Needs three-way integration (Gmail + Calendar + notes). Deferred to validate the pipeline with two simpler seeds first. | Wave 5. |
| OOS-W4-5 | Migrating existing Claude-native task types to capabilities (F-13) | Depends on F-11 seeding infra being mature; not coupled to Wave 4's capabilities. | When `news_check`/`email_triage` are trusted and the pattern has stabilized. |
| OOS-W4-6 | `html_extract` tool (non-RSS news sites) | Every news source the user cares about publishes RSS; pure-HTML news falls through to `web_fetch` + LLM parse. | When a concrete RSS-less caller appears. |
| OOS-W4-7 | Per-item alert expansion (`alerts: [...]` list) | Digest shape serves every current use case. Multi-DM-per-run would be noise. | When F-4 dashboard exists and structured per-item display matters. |
| OOS-W4-8 | `POST /admin/automations` FastAPI lifespan-level smoke | Per-wave E2E + cross-capability integration test cover the intent of F-14. | When a production readiness wave addresses Docker/lifespan-level invariants explicitly. |
| OOS-W4-9 | `email_triage` unbounded-sender mode (scan all inbound mail) | Different privacy shape + token cost profile — belongs as a separate capability. | When a concrete user ask arrives (captured as F-W4-A). |

---

## 3. Core Concepts and Vocabulary

**Since-last-run semantics.** Dispatcher-injected `prior_run_end` input allowing a skill to filter source data to items newer than the previous successful run. `null` on first-ever run (catch-up semantics). Replaces the per-automation skill-state blob option rejected during brainstorming.

**`prior_run_end`.** ISO-8601 timestamp of the most recent successful `automation_run.end_time` for a given automation. Queried at dispatch time; never persisted as its own column. Computed via `SELECT end_time FROM automation_run WHERE automation_id = ? AND status = 'ok' ORDER BY end_time DESC LIMIT 1`.

**Digest shape.** Skill final-output contract `{ok, triggers_alert, message, meta}`. Single rendered DM per run. Compatible with `product_watch` output schema. Wave 4 codifies this as the default shape for multi-hit capabilities.

**Read-only tool.** A skill-system tool that enforces write-prohibition at the wrapper boundary (not via OAuth scope alone). All Wave 4 tools are read-only: `rss_fetch`, `gmail_search`, `gmail_get_message`. Even if OAuth scope granted compose/send, the tool wrapper refuses.

**Capability-availability guard.** Precondition check in `AutomationCreationPath` that verifies all tools referenced by the matched capability's skill are registered before writing the automation row. Prevents the "approved-but-unrunnable" class of errors.

**Tool registration threading.** Pattern where startup wiring passes optional integration clients (e.g., `GmailClient`) into `register_default_tools` so tool availability is a function of the runtime environment, not a compile-time constant. First capability that depends on an optional tool (Gmail tools in Wave 4) establishes this pattern.

---

## 4. Architecture

### 4.1 No process split changes

Orchestrator + API split unchanged from Wave 2/3. New capabilities ride existing infrastructure: `AutomationDispatcher` handles scheduling; `SkillExecutor` dispatches skill runs; `NotificationService` DMs on `triggers_alert=true`. Wave 4 is **additive only** — no new services, no new process boundaries.

### 4.2 Component inventory

| Component | Role | New / Changed | File |
|---|---|---|---|
| `rss_fetch` tool | Parse RSS/Atom URL → `[{title, link, published, author, summary}]`. Optional `since` arg filters items server-side. Uses `feedparser` library. | **New** | `src/donna/skills/tools/rss_fetch.py` |
| `gmail_search` tool | Gmail query → `[{id, sender, subject, snippet, internal_date}]`. Thin wrapper around existing `GmailClient.search_emails`. Read-only enforced at wrapper boundary. | **New** | `src/donna/skills/tools/gmail_search.py` |
| `gmail_get_message` tool | Fetch full body + headers for a single message id. Thin wrapper around `GmailClient.get_message`. Read-only enforced. | **New** | `src/donna/skills/tools/gmail_get_message.py` |
| `register_default_tools` | Extended to register the three new tools. Takes optional `gmail_client: GmailClient | None` arg — when present, registers Gmail tools; when `None` (test/degraded mode), registers only `web_fetch` + `rss_fetch`. | **Changed** | `src/donna/skills/tools/__init__.py` |
| `news_check` capability | Capability row + input schema (`topics`, `feed_urls`, `since?`). Registered via `config/capabilities.yaml` + Alembic seed migration. | **New** | `skills/news_check/`, `config/capabilities.yaml`, alembic migration |
| `email_triage` capability | Capability row + input schema (`senders`, `query_extras?`, `since?`). Registered same way. | **New** | `skills/email_triage/`, `config/capabilities.yaml`, alembic migration |
| `AutomationDispatcher` | Extended to inject `prior_run_end` into skill inputs — queries the most recent `automation_run.end_time` for this automation before dispatch; `null` on first-ever run. ~10-line change + unit test. | **Changed** | `src/donna/automations/dispatcher.py` |
| `StartupContext` / `wire_skill_system` | Extended to pass `gmail_client` into `register_default_tools`. `GmailClient` is already constructed in the existing email subsystem wiring — we thread the handle through. | **Changed** | `src/donna/cli_wiring.py` |
| `AutomationCreationPath` | Extended with capability-availability guard (pre-approval check that all required tools are registered). | **Changed** | `src/donna/automations/creation_flow.py` |
| Followups doc | Mark F-W3-A/B/C/D/F/G/H/I/J/K closed with commit refs; add Wave 4 completion stub on merge. | **Changed** | `docs/superpowers/followups/2026-04-16-skill-system-followups.md` |

**Key point:** no changes to `SkillExecutor`, `ToolDispatcher`, `MockToolRegistry`, `DiscordIntentDispatcher`, `ChallengerAgent`, or the cadence policy. Those all generalize to the new capabilities via the existing registration patterns. Challenger discovery is automatic via `CapabilityMatcher` (embedding + keyword over `capability.description` + `input_schema`).

### 4.3 Data flow — new run of a Wave 4 automation

```
Scheduler tick
  │
  ▼
AutomationDispatcher.dispatch(automation)
  │
  ├─ Query prior_run_end:
  │    SELECT end_time FROM automation_run
  │    WHERE automation_id = ? AND status = 'ok'
  │    ORDER BY end_time DESC LIMIT 1
  │    → ISO-8601 string or null
  │
  ├─ Build skill inputs:
  │    { ...automation.inputs, prior_run_end: <queried> }
  │
  ▼
SkillExecutor.execute(capability, inputs, automation_run_id)
     (sandbox state → claude_native path)
     (shadow_primary state → SkillExecutor fires + Claude shadow in parallel)
  │
  ├─ Step 1 (tool): rss_fetch(url, since=prior_run_end)              [news_check]
  │                 OR gmail_search(query="from:X after:<ts>")       [email_triage]
  │    → structured list of items, already filtered to "new since last run"
  │
  ├─ Step 2 (LLM): classify items as action-required / noteworthy
  │    → filtered candidate list
  │
  ├─ Step 3 (LLM, email_triage only):
  │    Gated by condition state.classify_snippets.candidates != []
  │    For each candidate, call gmail_get_message(id) for body.
  │    Re-classify with full body.
  │
  ├─ Step 4 (LLM): render digest message + compute triggers_alert
  │    → { ok, triggers_alert, message, meta: {item_count, source_url, ...} }
  │
  ▼
AutomationDispatcher persists automation_run (with end_time)
  │
  ▼
NotificationService.notify(user_id, message)  [if triggers_alert]
  → Discord DM
```

### 4.4 Tool design detail

**`rss_fetch(url: str, since: str | null, max_items: int = 50)`** →

```json
{
  "ok": true,
  "items": [
    {"title": "...", "link": "...", "published": "ISO-8601", "author": "...", "summary": "..."}
  ],
  "feed_title": "...",
  "feed_description": "..."
}
```

- `since` is inclusive-exclusive contract: items where `published > since`. `null` → return all up to `max_items`.
- `feedparser` parses 99% of RSS/Atom. Falls back to `updated` field when `published` absent. If both absent, item's `published` is `None` and the tool returns it un-filtered (caller treats as "unknown age").
- Raises `ToolError` on unparseable input (no silent truncation).

**`gmail_search(query: str, max_results: int = 20)`** →

```json
{
  "ok": true,
  "messages": [
    {"id": "...", "sender": "Jane <jane@x.com>", "subject": "...", "snippet": "...", "internal_date": "ISO-8601"}
  ]
}
```

- `query` is raw Gmail query syntax (`from:jane@x.com after:2026/04/20`). Skill composes it from inputs + `prior_run_end`.
- Tool enforces `max_results ≤ 100`. `internal_date` converted from Gmail's epoch-ms to ISO-8601.

**`gmail_get_message(message_id: str)`** →

```json
{
  "ok": true,
  "sender": "...",
  "subject": "...",
  "body_plain": "...",
  "body_html": null,
  "internal_date": "ISO-8601",
  "headers": {"To": "...", "Cc": "..."}
}
```

- Returns plain-text body preferentially; HTML body only when no plain-text alternative.

**Read-only enforcement:** both Gmail tool wrappers explicitly call `GmailClient.search_emails`/`get_message` only. Never construct drafts, never touch compose. Code-level enforcement via the wrapper API; no reliance on OAuth scope limits alone.

### 4.5 Fixtures + `tool_mocks`

Each new capability ships with **4 fixtures** (matching the Wave 2 pattern, exceeding the `fixture_regression_min` gate of 3):

**`news_check`:**
- `news_with_new_items.json` — 5 items, 2 after `prior_run_end` → `triggers_alert=true`, digest lists 2.
- `news_no_new_items.json` — same feed, all items before `prior_run_end` → `triggers_alert=false`.
- `news_empty_feed.json` — feed parses ok, no items → `triggers_alert=false`.
- `news_feed_unreachable.json` — `rss_fetch` mock raises `ToolError` → skill escalates per `on_failure=escalate`.

**`email_triage`:**
- `email_two_action_required.json` — 3 matching emails, 2 classified action-required → digest lists 2, `triggers_alert=true`.
- `email_none_action_required.json` — 2 matching emails, neither action-required → `triggers_alert=false`.
- `email_zero_matches.json` — `gmail_search` returns empty list → `triggers_alert=false`.
- `email_gmail_error.json` — `gmail_search` mock raises → skill escalates.

Each fixture pins `tool_mocks` for every tool step. Follows the `fixture_tool_mocks` schema established in Wave 2.

### 4.6 Data model

**No schema changes.** Everything maps to existing tables:

- Capability rows → `capability` (inserted via Alembic seed migration).
- `prior_run_end` → computed from `automation_run.end_time` at dispatch time; not persisted anywhere new.
- Fixtures → `skill_fixture` (same shape as `product_watch` fixtures).
- Alerts → `NotificationService` → existing Discord DM channel.

This is deliberate. Wave 4 is an **additive replication** of the Wave 2 pattern. Zero schema changes is the test: if the Wave 2 framework generalizes, we need no migrations beyond seed-row inserts.

### 4.7 Observability

New `task_type`s emitted into `invocation_log` via `SkillExecutor._run_llm_step`:

- `skill_step::news_check::classify_items`
- `skill_step::news_check::render_digest`
- `skill_step::email_triage::classify_snippets`
- `skill_step::email_triage::classify_bodies` (when step 3 fires)
- `skill_step::email_triage::render_digest`

These resolve to routes via the longest-prefix-match fallback in `ModelRouter._resolve_route` (shipped as Task 0 in Wave 2). No new route config needed.

Tool invocations logged via `ToolDispatcher` as usual — `rss_fetch`, `gmail_search`, `gmail_get_message` each get their own `tool_invocation` rows keyed by skill step.

### 4.8 Failure modes

| Failure | Mitigation |
|---|---|
| RSS feed unreachable or malformed | `rss_fetch` raises `ToolError`; skill's `on_failure=escalate` (default) triggers Claude fallback. If Claude also fails, `automation_run.status='failed'`; EOD digest surfaces it. |
| Gmail token expired | `GmailClient` raises; `gmail_search` tool propagates as `ToolError`. Same escalation. User DM'd via existing skill-failure notification path. Refresh triggered via existing OAuth refresh loop; no new mechanism. |
| `prior_run_end` query returns `null` on first-ever run | Tools treat `since=null` as "no filter — return recent items." First run naturally returns a backlog; skill classifies the full set. Acceptable because first-run semantics are "catch up." Documented in each capability's description. |
| Skill classifies zero items as action-required but source returned N items | Not a failure. `triggers_alert=false`, `meta.item_count=N`, `meta.action_required_count=0`. No DM. Dashboard/logs show coverage. |
| User approves an `email_triage` automation before GmailClient is authenticated | `register_default_tools` skipped Gmail tool registration (`gmail_client=None`). `AutomationCreationPath` capability-availability guard catches this at approval time and DMs: *"I can't run `email_triage` until Gmail is connected — set that up first and try again."* |
| First-run digest is noisy (large backlog) | `render_digest` prompt caps digest at a reasonable character count with *"+N more."* tail. Future layer: per-notification length cap at `NotificationService` (F-W4-G). |
| LLM classifies same email as action-required on two consecutive runs | Can't happen — `prior_run_end` filter means the same email never hits the classifier twice (unless it arrives mid-run; §4.8 accepted boundary). |

### 4.9 Challenger discovery

No changes to `ChallengerAgent` or `prompts/challenger_parse.md`. New capabilities are automatically discoverable via `CapabilityMatcher` (embedding + keyword over `capability.description` + `input_schema`). Confidence below threshold falls through to `ClaudeNoveltyJudge` — covered by existing AS-W3.4 path.

If post-merge observation reveals `CapabilityMatcher` matches the wrong capability for ambiguous phrasings (e.g., *"watch my email for new articles"*), tune the description text in the seed migration and re-embed on restart. No Wave 4 code change required.

---

## 5. Deliverables

| # | Deliverable | Size | Depends on |
|---|---|---|---|
| **W4-D1** | Add `feedparser` to `pyproject.toml` + `uv.lock`. Pinned to a specific version. | XS | — |
| **W4-D2** | `rss_fetch` tool — `src/donna/skills/tools/rss_fetch.py` + unit tests covering: valid RSS parse, Atom parse, `since` filter, malformed feed → `ToolError`, empty feed. | S | W4-D1 |
| **W4-D3** | `gmail_search` + `gmail_get_message` tools — two thin wrappers over `GmailClient`. Unit tests using a `FakeGmailClient` covering: search returns summaries, `max_results` clamping, read-only enforcement (wrappers never construct draft/send calls), get_message plain-vs-html body preference. | S | — |
| **W4-D4** | `register_default_tools(gmail_client: GmailClient \| None = None)` — extended to register RSS + (conditionally) Gmail tools. `cli_wiring.py` threads existing `GmailClient` handle from the email subsystem into `wire_skill_system`. Unit test asserting both paths: with and without `gmail_client`. | S | W4-D2, W4-D3 |
| **W4-D5** | `AutomationDispatcher` — inject `prior_run_end` into skill inputs. Query the most recent successful `automation_run.end_time` for the automation. Inject as an input field before dispatch. Unit test covering: first-ever run → `null`; second run → ISO-8601 of previous end_time; failed previous run skipped (query filters `status='ok'`). | S | — |
| **W4-D6** | `news_check` capability + skill. Artifacts: `skills/news_check/skill.yaml`, `skills/news_check/steps/classify_items.md`, `skills/news_check/steps/render_digest.md`, `skills/news_check/schemas/classify_items_v1.json`, `skills/news_check/schemas/render_digest_v1.json`, `capabilities/news_check/input_schema.json`. Alembic migration inserts the capability row. | M | W4-D2 |
| **W4-D7** | `news_check` fixtures (4 per §4.5) with `tool_mocks` for `rss_fetch`. Seeded via Alembic migration alongside W4-D6. | S | W4-D6 |
| **W4-D8** | `email_triage` capability + skill. Artifacts: `skills/email_triage/skill.yaml`, `steps/classify_snippets.md`, `steps/classify_bodies.md`, `steps/render_digest.md`, `schemas/*_v1.json`, `capabilities/email_triage/input_schema.json`. Alembic migration inserts the capability row. Step 3 (`classify_bodies`) gated by condition on step 2's output. | M | W4-D3 |
| **W4-D9** | `email_triage` fixtures (4 per §4.5) with `tool_mocks` for `gmail_search` + `gmail_get_message`. Seeded alongside W4-D8. | S | W4-D8 |
| **W4-D10** | `AutomationCreationPath` capability-availability guard. Before writing the automation row, check that all tools referenced by the capability's skill are registered. If not, reject with Discord DM: *"I can't run `<capability>` until `<dependency>` is connected — set that up first and try again."* ~20 lines + unit test. | S | W4-D4, W4-D8 |
| **W4-D11** | Update `config/capabilities.yaml` to enumerate `news_check` + `email_triage` for `SeedCapabilityLoader`. (Parallels Wave 2's addition of `product_watch`.) | XS | W4-D6, W4-D8 |
| **W4-D12** | E2E `tests/e2e/test_wave4_news_check.py`. Scenarios: (a) NL "watch feed X for topic Y every 12 hours" → confirmation card → approve → automation row → tick → `rss_fetch` mock returns 2 new items after `prior_run_end` → Discord DM with 2-item digest; (b) second tick later, no new items → no DM; (c) promotion to `shadow_primary` fires `SkillExecutor` in parallel with Claude shadow. | M | W4-D6, W4-D7, W4-D11 |
| **W4-D13** | E2E `tests/e2e/test_wave4_email_triage.py`. Scenarios: (a) NL "watch for action-required emails from X every 12 hours" → approve → tick → `gmail_search` returns 3 messages, 2 classified action-required, digest DM'd; (b) `gmail_get_message` called only for the 2 candidates, not all 3; (c) Gmail disconnected before approval → W4-D10 guard rejects with DM. | M | W4-D8, W4-D9, W4-D11 |
| **W4-D14** | Cross-capability integration test `tests/e2e/test_wave4_full_stack.py`. Seeds one `product_watch` + one `news_check` + one `email_triage`. Runs a single scheduler tick. Asserts: (1) three separate `automation_run` rows; (2) no cross-pollination of `prior_run_end` values; (3) notification DMs dispatched for the alerting ones only; (4) no shared-state bugs (tool registry corruption, `tool_mocks` leakage across runs). | M | W4-D12, W4-D13 |
| **W4-D15** | Followups doc cleanup. Mark F-W3-A/B/C/D/F/G/H/I/J/K closed with commit references (`50794a1`, `9ae2b8d`). Add "Completed — Wave 4 (2026-04-20)" section stub. Surface any new followups discovered during Wave 4. | XS | — |

**Totals:** 3 XS, 7 S, 5 M. Roughly **4–6 focused days**.

### 5.1 Dependency graph

```
W4-D1 (feedparser dep) ──► W4-D2 (rss_fetch) ──┐
                                                │
W4-D3 (gmail tools) ────────────────────────────┤
                                                │
                                                ├──► W4-D4 (register_default_tools + wiring)
                                                │                │
                                                │                ▼
W4-D2 ───► W4-D6 (news_check cap) ──► W4-D7 (news fixtures) ──► W4-D11 ──► W4-D12 (news E2E)
                                                                                              │
W4-D3 ───► W4-D8 (email_triage cap) ──► W4-D9 (email fixtures) ──► W4-D11 ──► W4-D13 (email E2E)
                                                                                              │
W4-D4 + W4-D8 ──► W4-D10 (capability-availability guard)                                      │
                                                                                              ▼
                                                                                     W4-D14 (cross-capability)

W4-D5 (dispatcher prior_run_end)  [parallel track, merges before W4-D12/D13]
W4-D15 (followups cleanup)        [parallel track, any time]
```

### 5.2 Suggested execution order

**Day 1 — Foundations (parallelizable).**
- W4-D1 (feedparser dep).
- W4-D2 (rss_fetch tool).
- W4-D3 (Gmail tools).
- W4-D5 (dispatcher `prior_run_end`).
- W4-D15 (followups cleanup — can start now, update as wave progresses).

**Day 2 — Wire-up + capability shells.**
- W4-D4 (`register_default_tools` + `cli_wiring` threading).
- W4-D6 (news_check capability files + Alembic migration).
- W4-D8 (email_triage capability files + Alembic migration).

**Day 3 — Fixtures.**
- W4-D7 (news_check fixtures).
- W4-D9 (email_triage fixtures).
- W4-D11 (capabilities.yaml).

**Day 4 — Guard + E2E.**
- W4-D10 (capability-availability guard).
- W4-D12 (news_check E2E).
- W4-D13 (email_triage E2E).

**Day 5 — Cross-cutting.**
- W4-D14 (cross-capability integration test).
- Final polish; Wave 4 followups captured in W4-D15.

### 5.3 Subagent parallelism opportunities

Per `superpowers:dispatching-parallel-agents`:

- **Group A (Day 1 foundations):** W4-D1, W4-D2, W4-D3, W4-D5, W4-D15 — no shared files. Five independent tracks.
- **Group B (Day 2-3 capability build):** (W4-D6 → W4-D7) and (W4-D8 → W4-D9) are two independent sequences — run as two parallel agents.
- **Group C (Day 4 E2E):** W4-D12 and W4-D13 are independent test files against the same harness — run as two parallel agents.
- **Not parallelizable:** W4-D4 (wire-up) serializes on D2+D3. W4-D10 serializes on D4+D8. W4-D11 serializes on D6+D8. W4-D14 waits for D12+D13 to be stable.

---

## 6. Acceptance Scenarios

**AS-W4.1 — `news_check` NL creation + first-run alert.**
User DMs: *"watch https://www.technologyreview.com/feed/ for articles about AI safety or alignment every 12 hours."*
- `ChallengerAgent` matches `news_check` (`match_score ≥ 0.7`), extracts `feed_urls=[<url>]`, `topics=["AI safety", "alignment"]`, `schedule={cron="0 */12 * * *"}`.
- Confirmation card posted. User clicks **Approve**.
- `AutomationRepository.create` writes row with `capability_name='news_check'`, `active_cadence_cron="0 */12 * * *"` (within `sandbox` policy floor of 12h).
- First scheduler tick: `AutomationDispatcher` queries `prior_run_end` → `null` → injects into inputs. `SkillExecutor` (sandbox → claude_native) runs: `rss_fetch(url, since=null)` returns 50 items; LLM classifies 3 as topic-matching; digest step renders *"3 new articles on AI safety: [titles + links]"*.
- `triggers_alert=true` → `NotificationService` DMs the user.

**AS-W4.2 — `news_check` since-last-run filter.**
Same automation, second scheduler tick 12 hours later.
- `AutomationDispatcher` queries `prior_run_end` → ISO-8601 of previous run's `end_time`.
- `rss_fetch(url, since=<prev_end>)` returns only 1 item published after that timestamp.
- LLM classifies 0 as topic-matching.
- `triggers_alert=false`, no DM, `automation_run` row recorded with `meta.item_count=1, action_required_count=0`.

**AS-W4.3 — `news_check` promotion to `shadow_primary` fires `SkillExecutor`.**
Test harness seeds 20 successful shadow runs (bypassing the counter) → `SkillLifecycleManager` promotes `news_check` to `shadow_primary` → `CadenceReclamper` moves `active_cadence_cron` toward user's target.
- Next tick: `AutomationDispatcher` routes to `SkillExecutor` (not claude_native). Claude runs in shadow in parallel. `skill_divergence` row recorded. `automation_run.skill_run_id` populated.

**AS-W4.4 — `email_triage` NL creation + action-required digest.**
User DMs: *"let me know when I get emails from jane@x.com or the team@x.com list that need a reply, check every 12 hours."*
- Challenger matches `email_triage`, extracts `senders=["jane@x.com", "team@x.com"]`, `schedule={cron="0 */12 * * *"}`.
- User approves.
- First tick: `gmail_search(query="from:(jane@x.com OR team@x.com)", max_results=20)` returns 3 messages (no `after:` filter because `prior_run_end=null`).
- Step 2 classifies from snippets: 2 look action-required.
- Step 3 calls `gmail_get_message` for only those 2 → full-body re-classification confirms both.
- Digest renders: *"2 emails need a reply: 'Re: Q2 roadmap' from Jane (2h ago), 'Budget approval' from team@ (4h ago)."*
- `triggers_alert=true` → DM.

**AS-W4.5 — `email_triage` zero-candidates early exit.**
Second tick: `gmail_search(query="from:... after:<prev_end>")` returns 1 new message. Step 2 classifies it as **not** action-required (automated notification).
- Step 3 (body fetch) is skipped — condition `state.classify_snippets.candidates != []` is false.
- `triggers_alert=false`. No `gmail_get_message` invocation recorded in `tool_invocation`. No DM.
- Meta: `snippet_scanned_count=1, body_fetched_count=0`.

**AS-W4.6 — Gmail-not-connected guard rejects at approval.**
`GmailClient` is `None` at startup (no OAuth token). `register_default_tools` did not register `gmail_search`/`gmail_get_message`.
- User DMs the same email_triage request. Challenger matches `email_triage`. Confirmation card posted.
- On **Approve** click: `AutomationCreationPath` runs capability-availability guard → finds `gmail_search` not in `DEFAULT_TOOL_REGISTRY` → rejects with DM: *"I can't run `email_triage` until Gmail is connected — set that up first and try again."*
- No automation row written. `PendingDraft` discarded.

**AS-W4.7 — Cross-capability single tick.**
Seed three automations for the same user, all due at the same tick: one `product_watch`, one `news_check`, one `email_triage`.
- Scheduler fires all three in sequence (today's dispatcher is serial per tick).
- Three distinct `automation_run` rows land; `prior_run_end` queries return the correct per-automation value (not cross-contaminated).
- Tool registry holds stable references; `tool_mocks` for each run are scoped correctly (no leak from one run's mock to another's call site).
- Notification DMs fire for the alerting automations only.

**AS-W4.8 — RSS feed unreachable → escalate.**
`rss_fetch(url)` raises `ToolError("Unreachable: 502")`. Skill step `fetch_items` has `on_failure=escalate` (default).
- `SkillExecutor` escalates to Claude with full context. Claude attempts fallback (or declines). If declines: `automation_run.status='failed'`, surfaced in EOD digest.

**AS-W4.9 — Gmail token expired mid-run.**
`gmail_search` tool invokes `GmailClient.search_emails`, which raises `TokenExpiredError`.
- `gmail_search` translates to `ToolError`. Skill escalates. `GmailClient` refresh loop runs asynchronously; next scheduled run succeeds with fresh token. User DM'd *"I couldn't check email this run — Gmail auth refresh pending"* via existing skill-failure notification path.

**AS-W4.10 — First-ever run returns recent items (both capabilities).**
`prior_run_end=null`. `rss_fetch` + `gmail_search` treat `null` as "no filter, up to `max_items`/`max_results`".
- Skills classify the full returned set. First-run alert may be noisy (e.g., 3 action-required emails in the backlog). Documented behavior in each capability's description; acceptable for v1.

**AS-W4.11 — Challenger discovery via `CapabilityMatcher` — no code-level routing.**
Two varied phrasings hit the same capability:
- *"keep an eye on TechCrunch for new AI posts"* → `news_check` match (`match_score ≥ 0.6`).
- *"give me a heads-up on new emails from my boss"* → `email_triage` match (`match_score ≥ 0.6`).
- `CapabilityMatcher` uses `capability.description` + `input_schema` registered in the Alembic migration. No changes to `ChallengerAgent` or the parse prompt.
- Confidence below threshold falls through to `ClaudeNoveltyJudge`.

**AS-W4.12 — Cadence clamp at landing state.**
User requests `news_check` every 15 min. `news_check` lifecycle state is `sandbox` → policy floor 12h.
- Confirmation card shows: *target every 15 min / active every 12 hours — "I'll speed up when I learn it."*
- Approve → `target_cadence_cron="*/15 * * * *"`, `active_cadence_cron="0 */12 * * *"`.
- Promotion to `shadow_primary` via shadow runs → `CadenceReclamper` moves active → hourly → trusted → 15 min. (Same mechanism Wave 3 shipped; no new code — verifies the policy applies to new capabilities.)

---

## 7. Requirements Matrix

| # | Requirement | Section | Scenario | Status |
|---|---|---|---|---|
| W4-R1 | `rss_fetch` tool parses RSS/Atom feeds and returns structured items with `since` filter support. | §4.4 | AS-W4.1, AS-W4.2 | [ ] |
| W4-R2 | `gmail_search` tool wraps `GmailClient.search_emails` with read-only enforcement at the wrapper boundary. | §4.4 | AS-W4.4 | [ ] |
| W4-R3 | `gmail_get_message` tool wraps `GmailClient.get_message`, returns plain-text body preferentially. | §4.4 | AS-W4.5 | [ ] |
| W4-R4 | `register_default_tools` conditionally registers Gmail tools based on `gmail_client` availability. | §4.2 | AS-W4.6 | [ ] |
| W4-R5 | `AutomationDispatcher` injects `prior_run_end` from the most recent successful `automation_run.end_time`; `null` on first run. | §4.3 | AS-W4.2, AS-W4.10 | [ ] |
| W4-R6 | `news_check` capability seeded via Alembic migration + `config/capabilities.yaml`; discoverable by `CapabilityMatcher`. | §4.2 | AS-W4.1, AS-W4.11 | [ ] |
| W4-R7 | `email_triage` capability seeded via Alembic migration + `config/capabilities.yaml`; discoverable by `CapabilityMatcher`. | §4.2 | AS-W4.4, AS-W4.11 | [ ] |
| W4-R8 | Each Wave 4 capability ships 4 fixtures with `tool_mocks` covering happy path, empty-result path, and error path. | §4.5 | AS-W4.2, AS-W4.5, AS-W4.8 | [ ] |
| W4-R9 | `AutomationCreationPath` rejects approval when a required tool is unregistered; DMs an actionable error. | §4.8 | AS-W4.6 | [ ] |
| W4-R10 | Wave 4 capabilities land at lifecycle state `sandbox`; promotion to `shadow_primary` follows the Wave 1 gate unchanged. | §4.1 | AS-W4.3, AS-W4.12 | [ ] |
| W4-R11 | All Wave 4 skill runs emit `skill_step::<capability>::<step>` task_types to `invocation_log`; resolved by existing longest-prefix-match. | §4.7 | Structural | [ ] |
| W4-R12 | All Wave 4 skill outputs conform to the digest shape `{ok, triggers_alert, message, meta}`. `NotificationService` unchanged. | §4.3 | AS-W4.1, AS-W4.4 | [ ] |
| W4-R13 | Skill failures (tool error, malformed LLM output) escalate per `on_failure=escalate` (default from Wave 3 DSL). | §4.8 | AS-W4.8, AS-W4.9 | [ ] |
| W4-R14 | Cross-capability scheduler tick preserves isolation — no shared state, no tool-registry corruption, no `tool_mocks` leakage. | §4.2 | AS-W4.7 | [ ] |
| W4-R15 | `email_triage` step 3 (`classify_bodies`) only invokes `gmail_get_message` when step 2 yielded candidates. | §4.3 | AS-W4.5 | [ ] |
| W4-R16 | Existing cadence policy + `CadenceReclamper` apply to new capabilities without code changes. | §4.1 | AS-W4.12 | [ ] |
| W4-R17 | Followups doc marks F-W3-A/B/C/D/F/G/H/I/J/K closed with commit refs; adds Wave 4 completion stub on merge. | §4.2 | Structural | [ ] |
| W4-R18 | No schema changes (no new tables, no new columns) — Wave 4 proves the Wave 2 framework generalizes. | §4.6 | Structural | [ ] |

---

## 8. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| `feedparser` has a known CVE or maintenance gap. | Low | Med | `feedparser` is actively maintained (≥ 6.0.x), widely used. Pin to a specific version. If a CVE lands, the wrapper is ~30 LOC — swap for stdlib `xml.etree.ElementTree` on short notice. |
| Gmail OAuth token expires between wave-4 wiring and first real run. | Med | Low | `GmailClient` already has a refresh loop. W4-D4 threads the *existing* client — doesn't construct new auth. AS-W4.9 covers the runtime failure path. |
| `prior_run_end` injection breaks `product_watch` backward-compat. | Low | Med | `product_watch`'s skill YAML doesn't reference `prior_run_end` — it's inert extra input. Unit test W4-D5 asserts injection is additive. `test_wave2_product_watch.py` must still pass unchanged. |
| LLM classification of "action-required" is inconsistent across runs (non-determinism). | Med | Low | `prior_run_end` filter means the same email never hits the classifier twice unless mid-run boundary. User-facing noise bounded by cadence policy (12h sandbox floor) and `triggers_alert` gating. If false-positives accumulate: tune prompt, add failing fixture. |
| RSS feeds with no `published` field (badly-formed feeds). | Med | Low | `rss_fetch` falls back to `updated`; if both absent, returns un-filtered with `published=None`. Log a warning so we can tighten if a user-facing feed triggers it. |
| `email_triage` scope creep — user wants "emails from anyone, not just named senders." | Med | Med | Wave 4 explicitly scopes to a sender allow-list. Unbounded mode is a different capability. Captured as F-W4-A; do not expand Wave 4. |
| Cross-capability isolation bug (tool mocks from one test leak into another via `DEFAULT_TOOL_REGISTRY`). | Med | Med | Pre-existing risk (F-W2-B, P3). W4-D14 is the canary. If it flakes across parallel runs, escalate F-W2-B from P3 to P1 and add `ToolRegistry.clear()` + pytest fixture. |
| `CapabilityMatcher` matches wrong capability for ambiguous phrasings. | Low | Low | Escalation path (W3 `ClaudeNoveltyJudge`) handles ambiguous cases. If observed: tune capability descriptions in the seed migration (matcher re-embeds on restart). No Wave 4 code change. |
| First-ever-run backlog DMs a huge digest. | Med | Low | Surfaced on confirmation card; `render_digest` caps length with *"+N more."* tail. Future layer: F-W4-G notification-side cap. |
| `gmail_search` returns 20 messages but classifying 20 snippets in one LLM step exceeds local context budget. | Low | Low | Hard cap `max_results=20`; snippet-only classification is compact. If hit: add pagination later. Escalation path covers it today. |
| Wave 4 E2E flakiness from 20-run promotion threshold. | Med | Low | Same pattern as Wave 2 — fixture seeds 20 successful shadow runs deterministically (not real scheduling). AS-W4.3 uses this approach. |

---

## 9. Out-of-Wave Followups Surfaced by Wave 4

These land in `docs/superpowers/followups/2026-04-16-skill-system-followups.md` when Wave 4 merges.

- **F-W4-A** — `email_triage` unbounded-sender mode (scan *all* inbound mail for action-required). Different privacy shape + token cost profile. Wait for concrete user ask.
- **F-W4-B** — Pagination for `gmail_search` / `rss_fetch` when result set exceeds per-step context budget. Trigger: observed context-overflow escalations on either capability.
- **F-W4-C** — `html_extract` tool for non-RSS news sites (deferred from Wave 4 scope per OOS-W4-6). Trigger: a concrete user-named non-RSS source.
- **F-W4-D** — Per-automation skill-state blob (the option-1 rejected during brainstorming). Revisit only if since-last-run semantics prove insufficient for a capability we want to build.
- **F-W4-E** — Dashboard surfacing of `meta.item_count` / `meta.action_required_count` / per-run diagnostics. Depends on F-4. Wave 5+.
- **F-W4-F** — `ToolRegistry.clear()` + pytest conftest fixture for test isolation (upgrading F-W2-B from P3 if W4-D14 surfaces flakiness).
- **F-W4-G** — First-run digest backlog length cap at the `NotificationService` layer (today enforced in the skill's render prompt; eventually belongs in the notification layer as generic protection).

---

## 10. Deferred to Future Waves (captured during this brainstorm)

These candidates were surfaced during Wave 4 brainstorming and deferred for separate sessions:

- **Wave 5+ candidate: Dashboard UI (F-4).** Separate brainstorm track — needs its own UI approach decision (new SPA vs. extend donna-ui). The whole "human retains judgment-level control" story collapses without this, but it's a separate project.
- **Wave 5+ candidate: Event triggers / `on_event` (OOS-1) + NL auto-modify existing automations (OOS-W3-8).** Expand the NL automation surface. Trigger: a concrete push-only source appears, or user complaints about missing edit-via-NL.
- **Wave 5+ candidate: Migrate existing Claude-native task types to capabilities (F-13).** `generate_digest`, `prep_research`, etc. Depends on F-11 seeding infrastructure being mature — Wave 4 validates maturity.
- **Wave 5+ candidate: `meeting_prep` capability.** Needs three-way integration (Gmail + Calendar + notes). Deferred from Wave 4 to first validate the pipeline with two simpler seeds.

---

## 11. Predecessor Spec Touchpoints

Wave 4 advances the following original-spec requirements and followups:

| Original req / followup | Status after Wave 4 |
|---|---|
| F-11 — Seed real capabilities for real usage | Advanced. Wave 2 seeded one (`product_watch`); Wave 4 seeds two more. F-11 remains open (ongoing as users identify needs) but the seeding pattern is now validated across three capabilities. |
| F-13 — Migrate existing Claude-native task types | Not addressed. Explicitly deferred (OOS-W4-5) — Wave 4 validates new-capability seeding; F-13 migrates existing ones. |
| F-W3-A through F-W3-K (Wave 3 P2/P3 followups) | All closed via commits `50794a1` + `9ae2b8d` (2026-04-17). Wave 4's W4-D15 updates the followups inventory to reflect this. |
| AS-5.1 (original) | Already closed by Wave 3. Wave 4 exercises the same path with two additional capabilities. |

---

## 12. Glossary (delta from Wave 3)

- **Since-last-run semantics** — Dispatcher-injected `prior_run_end` input allowing a skill to filter source data to items newer than the previous successful run. `null` on first-ever run (catch-up semantics).
- **`prior_run_end`** — ISO-8601 timestamp of the most recent successful `automation_run.end_time` for a given automation. Queried at dispatch time; never persisted as its own column.
- **Digest shape** — Skill final-output contract `{ok, triggers_alert, message, meta}`. Single rendered DM per run; compatible with `product_watch` output schema. Wave 4 codifies this as the default shape for multi-hit capabilities.
- **Read-only tool** — A skill-system tool that enforces write-prohibition at the wrapper boundary (not via OAuth scope alone). All Wave 4 tools are read-only: `rss_fetch`, `gmail_search`, `gmail_get_message`.
- **Capability-availability guard** — Precondition check in `AutomationCreationPath` that verifies all tools referenced by the matched capability's skill are registered before writing the automation row. Prevents "approved-but-unrunnable" errors.
- **Tool registration threading** — Pattern where startup wiring passes optional integration clients (e.g., `GmailClient`) into `register_default_tools` so tool availability is a function of the runtime environment, not a compile-time constant.
