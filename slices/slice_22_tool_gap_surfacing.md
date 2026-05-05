# Slice 22: Tool Gap Surfacing

> **Goal:** When `capability_tool_check` finds a missing tool, route it to a `tool_request` row. High-blocking gaps (capability is active and cannot run) ping the user in real time. Speculative gaps (registered-but-not-scheduled, or proposed by a skill draft) file silently and surface in the morning digest. Tool *builds* — when the user decides to fulfill a request — reuse slice 21's claude_code mode protocol, plus extra security lints from §10.5.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §7 (tool gap protocol — full), §8 (`tool_request` schema), §10.5 (tool-build-specific failures — all rows), §10.10 (`tool_gap_detected`, `tool_request_filed` audit logs).
**Related upstream specs:** `spec_v3.md §3.2` (Tool Integration Architecture), `spec_v3.md §23.3` (Skill Executor and Tool Registry).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §7 — Tool gap protocol

Tools cannot be auto-drafted (security, dependencies, credentials, image rebuild). When `capability_tool_check` finds a missing tool, Donna takes one of two paths based on **blocking severity**:

| Blocking severity | Trigger | Surfacing |
|---|---|---|
| **High** | Capability is active (scheduled or user-invoked) and cannot run | Real-time Discord ping with `[File request] [Snooze 24h]` |
| **Speculative** | Capability is registered but not yet scheduled, OR a skill draft proposed using a not-yet-existing tool | Filed silently to `tool_request` table; surfaces in morning digest |

Both paths write a `tool_request` row. Tool *builds* use the same claude_code protocol as §5.3 (slice 21) but with extra checks (§10.5).

Crucially: the **decision to start a tool build is always the user's**. A real-time ping is a notification, not an escalation request — there are no `[Approve $X extension]` buttons because no API spend will fix the gap.

### §8 — `tool_request` schema

```sql
CREATE TABLE tool_request (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  proposed_signature JSON,
  rationale TEXT,
  blocking_capability_id INTEGER,    -- NULL = speculative
  priority INTEGER DEFAULT 3,
  status TEXT DEFAULT 'open',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMP,
  resolved_branch TEXT
);
```

### §10.5 — Tool-build-specific failures (all rows wire here)

| Failure | Mitigation |
|---|---|
| Tool needs new dependency, image not rebuilt | Tool-build template requires `requires_rebuild: bool` in tool metadata. If true after merge: registry refuses to mark tool active until orchestrator restart with new build SHA. Discord nag posted hourly until rebuild. |
| Tool hardcodes a credential value | Pre-commit secret scanner runs on the branch before validation. Common patterns blocked. Vault-key naming convention flagged. |
| Tool calls Anthropic API directly | Lint check: `import anthropic` outside `src/donna/llm/` is hard fail. |
| Tool not added to any agent allowlist | Lint check. Submission requires at least one allowlist update or explicit `unallowlisted=true` flag. |
| Tool does I/O at import time | Lint check: tool module's top-level scope must not invoke network/disk APIs. Heuristic + explicit `is_inert_at_import` test fixture. |
| Tool unbounded latency | Per-tool `default_timeout_seconds` declared in metadata; dispatcher enforces. Default 5s. |

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §7, §8, §10.5
- Slice 21 — depends on it (tool builds reuse claude_code protocol)
- `src/donna/capabilities/capability_tool_check.py`, `tool_requirements.py` — emission point
- `src/donna/skills/mock_tool_registry.py` — mock entry verification
- `prompts/escalation/tool_build.md` — new Jinja template (canonical §9)
- `src/donna/skills/morning_digest.py` (or equivalent) — speculative gap aggregation

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- Does the morning digest already exist? If yes: extend it to include speculative tool gaps. If no: speculative gaps wait for a future digest slice — fall back to a "Daily tool-gap summary" Discord post for now.

## Not in Scope

- Building any specific tool (browser, etc.).
- Auto-drafting tools — explicitly forbidden by §7 / §10.5.
- Multi-user tool-request dedup (Phase 2).

## Session Context

Load only: `CLAUDE.md`, this brief, canonical spec, slice 21 outputs, `capability_tool_check.py`, `tool_requirements.py`, `mock_tool_registry.py`, the new `prompts/escalation/tool_build.md`.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Define "blocking severity" precisely — what's the data shape passed from `capability_tool_check` to the surfacing layer?
- [ ] Decide morning-digest integration vs standalone "tool gap" Discord post (per open question).
- [ ] Choose the secret scanner — reuse pre-commit hook config? Add `detect-secrets` library? Custom regex?
- [ ] Lint check implementation: AST-based (proper) or grep-based (cheaper)? Both should reject `import anthropic` outside `src/donna/llm/`.
- [ ] Where does `is_inert_at_import` test fixture live — `tests/tools/`? A shared `tools/conftest.py`?
- [ ] Snooze mechanic: store snooze state on `tool_request` row, or a separate `tool_request_snooze` table?
- [ ] Dedup: a re-emission of the same tool gap should bump priority + update rationale, not create a second row. Define the dedup key (`tool_name + user_id`?).
- [ ] What does `proposed_signature` JSON look like — roughly mirror Python type hints, OpenAPI shape, or something custom?

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the tool-gap protocol differ from §7? Update §7.
- [ ] Did the schema differ from §8? Update §8.
- [ ] Did the failure mitigations differ from §10.5? Update §10.5.
- [ ] Did the new prompt template differ from §9? Update §9.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did `spec_v3.md §3.2` / §23.3 stubs need updating to reflect what shipped? Update them.
