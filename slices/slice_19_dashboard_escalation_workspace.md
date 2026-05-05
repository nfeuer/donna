# Slice 19: Dashboard Escalation Workspace

> **Goal:** Stand up the canonical surface for viewing and submitting escalations: `/admin/escalations` list view + `/admin/escalations/<correlation_id>` detail view + submit endpoint. This slice ships BEFORE the chat and claude_code modes (slices 20, 21) — they require this surface to exist. Submit endpoint is mode-agnostic for slice 19; mode-specific UIs (textarea vs "Mark as built" modal) attach in their respective slices.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §6.3(b) (escalation workspace), §5.2 / §5.3 dashboard surfaces (the *generic* parts — mode-specific UIs land in slices 20/21), §11 functional ACs related to dashboard rendering and submission, §10.7 row 1 (toggle race / optimistic lock infrastructure used by the submit endpoint too).
**Related upstream specs:** `spec_v3.md` is silent on the dashboard escalation page; this is net-new surface area. The existing `docs/domain/management-gui.md` patterns apply (admin section, no auth, dark theme, 30s refresh).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §6.3(b) — Escalation workspace

New dashboard area at `/admin/escalations`:

- **List view**: all escalation_requests with status filter (`open | submitted | validated | failed | cancelled`), sort by age, expandable rows showing the summary inline.
- **Detail view** at `/admin/escalations/<correlation_id>`:
  - Full prompt rendered with one-click copy
  - Mode-specific submission UI:
    - `chat`: textarea + Submit button (slice 20 attaches behavior)
    - `claude_code`: copy worktree command, copy spec, "Mark as built" modal (slice 21 attaches behavior)
  - Status timeline (each `invocation_log` lifecycle row)
  - Validation result panel (post-submission)
  - Re-submit affordance if validation failed (within iteration cap)
- **Auth**: same auth as the rest of admin dashboard. Multi-user-ready via `user_id` filter on the list view.

### §5.2 / §5.3 — Dashboard surfaces this slice ships generically

- Full prompt rendered (markdown → HTML, syntax-highlighted code blocks).
- One-click **Copy prompt** button (works for both modes).
- Estimate, daily remaining, task_type, age display.
- Status timeline component (consumes `invocation_log` rows).
- POST endpoint `/admin/escalations/<correlation_id>/submit` accepting a JSON payload with `mode`-discriminated body (validated against `schemas/escalation_submission.json`).

### §11 — Functional ACs realized by this slice

- [ ] Dashboard `/admin/escalations/<id>` page renders prompt, accepts answers (chat) or branch confirmations (claude_code) — chat textarea + claude_code "Mark as built" modal land in slices 20/21, but the page scaffolding + endpoint ship here.

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §6.3(b), §5.2, §5.3
- `docs/domain/management-gui.md` — convention anchors (no auth, dark theme, 30s refresh, card-on-Dashboard pattern)
- `docs/superpowers/plans/2026-04-21-wave-4-skill-system-ui.md` — most recent dashboard slice; the patterns it sets (PageHeader + filters + table + drawer) should be reused
- `donna-ui/src/pages/Shadow/index.tsx` — closest existing analogue for the list+detail pattern
- `donna-ui/src/api/` — fetcher module pattern
- `src/donna/api/routes/admin_dashboard.py` — backend surface
- Slice 17's `escalation_request` schema is the data source

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- Dashboard auth: spec says "same auth as rest of admin dashboard". `docs/domain/management-gui.md` says "no auth". Confirm which is canonical for this homelab single-user phase.
- Should the submit endpoint live under `/admin/...` or `/api/escalations/...`? The convention split in `admin_dashboard.py` may already answer this.

## Not in Scope

- Mode-specific submission UI behavior (slices 20, 21 attach textarea logic and Mark-as-built modal logic).
- Toggle control panel (slice 23).
- Validation result rendering content — slice 19 ships the *panel*; what populates it is slices 20/21.
- Budget extension UI (slice 18 + slice 23 split).

## Session Context

Load only: `CLAUDE.md`, this brief, the canonical spec, `docs/domain/management-gui.md`, `docs/superpowers/plans/2026-04-21-wave-4-skill-system-ui.md` (latest dashboard pattern reference), slice 17's outputs, the existing `donna-ui/src/pages/Shadow/index.tsx` page as the pattern to mirror.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Confirm dashboard auth model (per open question above).
- [ ] Define `schemas/escalation_submission.json` shape — discriminated union by mode? Single envelope with optional fields?
- [ ] Decide how the status timeline reads `invocation_log` rows efficiently — direct DB read vs a derived `escalation_event` table.
- [ ] Pick the markdown renderer (existing in donna-ui or add `react-markdown` + `rehype-highlight`).
- [ ] Page-level optimistic lock semantics: if user A is viewing detail page while user B (or background process) advances state, how does the page reconcile?
- [ ] Re-submit affordance: same correlation_id with iteration++, or a new `escalation_request` row with `parent_escalation_id`? (Spec §8 has `parent_escalation_id` for this.)
- [ ] Mobile responsiveness scope — full responsive, or admin-desktop-only matching existing dashboard?
- [ ] Define what "Validation result panel" displays when status='open' (placeholder vs hidden).

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the dashboard surface differ from §6.3(b)? Update §6.3(b).
- [ ] Did the submit endpoint contract differ from §5.2/§5.3? Update those.
- [ ] Did the schema for the submission payload differ from §9 (`schemas/escalation_submission.json`)? Update §9.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did `docs/domain/management-gui.md` need a new subsection? Add it.
