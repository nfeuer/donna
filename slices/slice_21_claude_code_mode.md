# Slice 21: Claude Code Mode

> **Goal:** Wire `claude_code` mode end-to-end. User receives a Discord ping with a dashboard link; copies the worktree command and spec from the dashboard; runs Claude Code locally; commits to the spec'd branch; clicks "Mark as built" in the dashboard. Donna ingests the branch, runs the existing `ValidationExecutor` against fixtures, promotes the skill (or rejects with iteration). Iteration cap = 3.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §5.3 (claude_code mode protocol — full), §10.3 (manual-handoff submission failures, all rows), §10.4 rows 1–2 (validation failures + iteration cap), §10.10 (`escalation_submitted`, `escalation_validated`, `escalation_failed`, `iteration_limit_reached` audit logs).
**Related upstream specs:** `spec_v3.md §13.1` (Budget Rules), `spec_v3.md §23.4` (skill lifecycle: sandbox → shadow → trusted), `spec_v3.md §3.2 / §23.3` (tool registry — manual builds for *skills* go through here; *tool* builds are slice 22's domain even though they share this protocol).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §5.3 — Claude Code mode protocol

Used for `task_types` whose output is code or files: `skill_draft`, `skill_evolution`, `tool_request_fulfillment`. Surface split: dashboard is canonical workspace; Discord is alert. Spec file is also written to disk because the user needs filesystem access to do the work anyway.

Donna → user:
1. Write spec file to `${DONNA_WORKSPACE_PATH}/escalations/<correlation_id>.md` containing: task summary, acceptance criteria, target file paths, reference module path, exact `git worktree add` + branch name commands, forbidden patterns.
2. Mirror spec into `escalation_request.prompt_body`.
3. Discord notification: short summary, correlation ID + dashboard link, optional MD attachment of spec, escalation buttons.

Dashboard escalation detail page (this slice attaches behavior to slice 19's scaffold):
- Full spec rendered.
- **Copy spec** button — paste straight into Claude Code.
- Pre-filled `git worktree add` command in a copy-on-click block.
- Branch name, target paths, reference module — all copyable.
- **Mark as built** button — modal asking for local branch SHA or push confirmation.
- Validation result panel populated post-submission with pass/fail per fixture, lint outcomes.

User → Donna: opens dashboard, copies worktree command, runs it, opens Claude Code in worktree, pastes spec, builds skill + tests, runs `pytest`, commits, clicks Mark as built (or `/donna submit <correlation_id> --branch <name>` from Discord).

Donna ingestion (mirrors `manual_draft_poller`):
1. Polls `escalation_request` rows where `submitted_at IS NOT NULL AND status = 'submitted'`.
2. Verifies branch exists (local or remote).
3. Diffs branch against base; validates touched paths match the spec's declared targets.
4. Routes through existing validation pipeline:
   - Skills: `ValidationExecutor` against fixture set; sandbox → shadow → trusted ladder unchanged.
   - Tools: lint check (mock entry, allowlist update, inert-at-import) → validation. (Tool-specific lint detail lives in slice 22.)
5. On pass: existing skill/tool registry update path. On failure: post failures back to Discord; user iterates in same worktree; resubmit triggers same pipeline. Iteration cap `manual_iteration_limit` (default 3).

### §10.3 — Manual-handoff submission failures (all rows wire here)

| Failure | Mitigation |
|---|---|
| User submits empty / malformed answer in chat mode | (Slice 20 — chat-specific.) |
| User submits but never builds the branch in claude_code mode | Poller checks `branch_exists(branch_name)` before processing. If absent after 5 min: posts "branch not found, did you push? or run /donna submit-local if local-only". |
| User pushes a branch with wrong files | Diff-validator rejects with specific list of out-of-scope files. User edits and resubmits; iteration count increments. |
| User force-pushes branch between submission and validation | Resolution locked to SHA at submission time. New SHA = new submission required. |
| Branch contains uncommitted/staged changes mixed with the work | Diff is computed against `base..tip`, ignoring working tree. |

### §10.4 rows 1–2 — Validation failures

| Failure | Mitigation |
|---|---|
| Skill from manual handoff fails fixture validation | Failures posted to Discord; same correlation thread. User iterates in worktree, resubmits. Iteration cap (3). |
| At iteration cap, still failing | Auto-cancel the escalation; create `human_review_request` row (or log with `human_review` flag). Dashboard surfaces. No infinite loop. |

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §5.3, §10.3, §10.4
- Slices 17 (core), 19 (dashboard workspace), 20 (chat mode for shared submit endpoint contract)
- `src/donna/skills/manual_draft_poller.py` — polling pattern to mirror
- `src/donna/skills/lifecycle.py` — `human_approval` transition exists; manual handoffs use it
- `src/donna/skills/validation_executor.py` + `mock_tool_registry.py` — validation pipeline
- `prompts/escalation/skill_draft.md` — new Jinja template (canonical §9)

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- Spec §12 Q2 — local-only branch read access. Does the orchestrator have a mount on the host repo's `.git` directory? If not, this slice adds one or restricts to pushed branches.
- Spec §12 Q3 — `human_review_request` table vs reusing `tool_request`. This slice forces the decision.

## Not in Scope

- Tool builds end-to-end (slice 22) — though the validator skeleton this slice ships will be reused there.
- Browser tool / any specific tool build.
- Dashboard runtime override UI (slice 23).

## Session Context

Load only: `CLAUDE.md`, this brief, canonical spec, slices 17 / 19 / 20 outputs, `manual_draft_poller.py`, `validation_executor.py`, `lifecycle.py`, the new `prompts/escalation/skill_draft.md` template.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Confirm `worktree` strategy: dedicated `worktrees/` directory vs sibling-of-repo. Does the dashboard's copy-on-click block hardcode this path, or read it from config?
- [ ] Decide `branch_exists` polling cadence (every 5 min? every minute for 10 min then back off?).
- [ ] Define the diff validator's "scope" precisely — exact path match, prefix match, or glob from `target_paths`?
- [ ] Resolve §12 Q3: one queue (`escalation_request` with `human_review` flag) or two (`escalation_request` + `human_review_request`)?
- [ ] Iteration counter semantics: each resubmit increments? Or each rejection?
- [ ] Failure feedback to Discord: full pytest output (could be 10s of KB), summary, or link to dashboard?
- [ ] How does the dashboard's "Mark as built" modal verify the branch SHA? Run `git ls-remote` against the local repo? Or trust the user-entered SHA and validate at poller pickup?
- [ ] Re-escalation parent chains (spec §12 Q5) — depth limit? Spec leaves it open.

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the claude_code protocol differ from §5.3? Update §5.3.
- [ ] Did the failure mitigations differ from §10.3 / §10.4 rows 1–2? Update them.
- [ ] Did the new prompt template / schema paths differ from §9? Update §9.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did the §12 open questions get resolved here? Move them out of §12 into a §15 decision entry with date.
- [ ] Did `docs/domain/skill-system.md` need a "Manual escalation" subsection update? Add it.
