# Documentation Cleanup — Design Spec

> **Goal:** Clean up Donna's existing documentation so it reads as authoritative current-state reference, with all "not implemented" / "deferred" / "obsolete" callouts extracted to a tracked backlog and historical content archived.

## Context

The documentation scores 78/100 overall — writing quality and coverage are strong, but readability suffers because inline hedging language ("not yet wired", "SUPERSEDED", "dead code", "deferred to slice X") interrupts the narrative throughout. The same problem affects `spec_v3.md`, where v3.1 reconciliation notes are scattered across 15+ locations in a 4,257-line file.

**Audience:** Both human (Nick browsing the MkDocs site) and AI (Claude reading docs for codebase context). The docs site should read cleanly; historical context stays accessible in archive directories.

**Approach:** Surgical Cleanup (edit in place, extract gaps, archive stale sections).

## Scope

Everything:
- `docs/domain/*.md` (22 files)
- `docs/architecture/*.md`, `docs/operations/*.md`, `docs/workflows/*.md`, `docs/development/*.md`, `docs/start-here/*.md`
- Top-level docs: `index.md`, `glossary.md`, `feature-map.md`, `changelog.md`, `troubleshooting.md`
- `spec_v3.md` (canonical spec)
- `IMPLEMENTATION_GUIDE.md`
- `docs/superpowers/specs/followups.md`
- MkDocs nav (`properdocs.yml`)

## 1. Expand open-backlog.md as the Consolidated Gap Tracker

All inline "not done" items extracted from docs get added to `docs/superpowers/followups/open-backlog.md`, organized into three tiers:

### Tier structure

**Critical (blocks a feature):**

| ID | Feature | Current State | What's Blocking | Source Doc | Spec § |
|----|---------|--------------|-----------------|-----------|--------|
| G-1 | GmailClient wiring | Not wired into orchestrator boot | email_triage automation can't run | agents.md | §12.1 |
| G-2 | SkillSystemConfig dead code | Pydantic model exists, fields not read by runtime | Thresholds hardcoded as module constants | skill-system.md | §23 |

**Partial (shipped with gaps):**

| ID | Feature | What's Shipped | What's Missing | Source Doc | Spec § |
|----|---------|---------------|---------------|-----------|--------|
| G-10 | Priority escalation | Deadline + workload pressure | Dependency-chain, user-lock flag | task-system.md | §5.5.2 |
| G-11 | Scheduling conflict resolution | Basic overlap detection | Priority displacement, cascade-shift, dual-invite | scheduling.md | §6.2 |
| G-12 | Time windows | 6 of 8 live | Extended Work, Emergency Work | scheduling.md | §6.1.2 |
| G-13 | Observability DB | invocation_log in donna_tasks.db + Loki | donna_logs.db not implemented | observability.md | §14.3.1 |
| G-14 | Notification tiers | Discord DM (tier 1-2) | Email tier 3 | notifications.md | §11.1 |
| G-15 | Budget breach handling | Pause-only behavior | 4-button escalation decision tree (slices 17-24) | handle-budget-breach.md | §18 |

**Deferred / Phase 6 (not started, by design):**

| ID | Feature | Rationale | Trigger Condition | Spec § |
|----|---------|-----------|-------------------|--------|
| G-20 | MCP Tier 2 (FastMCP) | Only Tier 1 needed currently | User needs GitHub/Notes/SearXNG | §3.2 |
| G-21 | Coding Agent | Safety gate | Code generation use case arises | §7.1.1 |
| G-22 | Communication Agent | Safety gate | Email/message drafting use case arises | §7.1.1 |
| G-23 | Off-server backup (GCS/Backblaze) | Local NVMe sufficient | Disaster recovery requirement | §16.3.2 |
| G-24 | Flutter app | API shipped, UI in sibling repo | Mobile use case | §20 |
| G-25 | donna_logs.db dedicated log DB | Loki pipeline works | Need SQLite-queryable structured logs | §14.3.1 |

Items already tracked in the existing open-backlog.md or followups.md are cross-referenced, not duplicated. When the same item exists in both, open-backlog.md is the canonical tracker (one-line summary with ID); followups.md retains the detailed audit trail and links back to the backlog ID.

## 2. Domain Doc Cleanup Pattern

For each domain doc with inline gap callouts:

### Step 1 — Identify callouts

Target patterns:
- "not yet implemented" / "not yet wired" / "not yet active"
- "SUPERSEDED" / "obsolete" / "dead code"
- "deferred to slice X" / "deferred to Phase X"
- "dormant in production" / "stub" / "placeholder"
- Multi-paragraph status blocks (e.g., "v3.1 Implementation Status: ...")

### Step 2 — Extract to open-backlog.md

Each callout becomes a line item with: ID, feature name, current state, what's missing, source doc, spec §.

### Step 3 — Replace with one-liner

The multi-paragraph callout becomes a single status line:

**For missing features:**
> `*Not yet active — see [open-backlog](../superpowers/followups/open-backlog.md#g-2)*`

**For partial implementations (in tables):**
> `Extended Work | 18:00–22:00 | *Not yet configured* |`

**For features documented but disabled:**
> `| Coding | *Defined, disabled — Phase 6 gate* |`

### Step 4 — Archive purely historical sections

Content that only explains past decisions or superseded manual steps moves to `docs/domain/archive/<original-name>-history.md`. Examples:
- skill-system.md Phase 3-5 manual activation steps (now automated by `wire_skill_system()`)
- Any "SUPERSEDED (date)" blocks

### Step 5 — Fix bugs

- **cost.md lines 96-98:** Remove copy-pasted skill-system.md content about §4.1/§4.2
- **backup-recovery.md:** Replace stub with proper content (inline the RECOVERY.md content or write a summary)
- **slices.md:** Update slice 15 status (no longer "in flight")

### Files requiring changes

| File | Issue Count | Primary Problem |
|------|-------------|----------------|
| skill-system.md | 5+ | SUPERSEDED sections, dead code callouts, Phase 3-5 not-yet-active |
| cost.md | 2 | Copy-paste error + deferred regression testing callout |
| agents.md | 2 | 2 unimplemented agents documented inline |
| observability.md | 2 | Aspirational donna_logs.db mixed with current Loki setup |
| memory-vault.md | 4 | Dormant code branch, deferred slice callouts |
| model-layer.md | 4 | Deferred features listed inline |
| notifications.md | 1 | Email tier 3 deferred callout |
| management-gui.md | 1 | Tool gap queue UI deferred callout |
| handle-budget-breach.md | 1 | Core content marked temporary |
| backup-recovery.md | 1 | Stub file |
| slices.md | 1 | Slice 15 "in flight" marker |

### Files needing no changes

chat.md, collection.md, insights.md, integrations.md, preferences.md, replies.md, scheduling.md, setup.md, task-system.md, api.md, llm.md, orchestrator.md, plus most of architecture/*, operations/* (except backup-recovery), workflows/* (except handle-budget-breach), start-here/*.

## 3. spec_v3.md Cleanup

### 3a. Add §0 Implementation Status Matrix

A new section at the top of spec_v3.md, after the version header, consolidating all v3.1 reconciliation notes into a single table:

```markdown
## §0 Implementation Status (v3.1, April 2026)

This matrix summarizes implementation state for every spec section.
Sections not listed are fully shipped as specified.

| Section | Status | Notes | Backlog |
|---------|--------|-------|---------|
| §3.2-3.3 MCP Strategy | Deferred | Tier 1 only in production | [G-20] |
| §5.5.2 Priority Escalation | Partial | Deadline + workload live | [G-10] |
| §6.1.2 / §6.2 Scheduling | Partial | 6/8 windows, 3/5 strategies | [G-11, G-12] |
| §7.1.1 Coding Agent | Defined, disabled | Phase 6 gate | [G-21] |
| §7.1.1 Communication Agent | Defined, disabled | Phase 6 gate | [G-22] |
| §11.1 Notifications | Partial | Discord only; email deferred | [G-14] |
| §12.1 Integrations | Partial | 6/10 integrations live | [G-20] |
| §14.3.1 Logging | Changed | Uses Loki, not donna_logs.db | [G-25] |
| §16.3.2 Backup | Partial | Local NVMe only | [G-23] |
| §20 Phases | Phases 1-5 complete | Phase 6 in appendix | — |
```

### 3b. Remove inline status blocks

Delete every "**v3.1 Implementation Status:**" block from the body of the spec. The section reads as pure design intent; the §0 matrix is the single source of truth for what's shipped.

### 3c. Move Phase 6 aspirational content to appendix

Sections describing entirely unbuilt features (MCP Tier 2 integration workflows, Coding Agent tool definitions, Communication Agent message flows, Flutter app architecture details) move to a new appendix:

```markdown
## Appendix: Phase 6 — Future Design

> The following sections describe planned features that are not yet implemented.
> They are preserved as design intent for when implementation begins.

### A.1 MCP Tier 2 Integration (from §3.2)
...

### A.2 Coding Agent Workflows (from §7.1.1)
...
```

Original locations get a pointer: `*See [Appendix A.2](#a2-coding-agent-workflows).*`

### 3d. Cross-reference to open-backlog.md

Each row in the §0 matrix links to the corresponding backlog item ID.

## 4. followups.md Cleanup

- Move completed/closed slice follow-ups (S18-S24 closed items) to `docs/superpowers/specs/archive/followups-closed-slices.md`
- Keep only genuinely open items in followups.md
- Cross-reference open items to the expanded open-backlog.md where they overlap
- Resulting file should be <200 lines (down from 1,047)

## 5. MkDocs Nav Updates

Changes to `properdocs.yml`:
- Exclude `docs/domain/archive/` from main navigation (accessible via search and direct links)
- Add `open-backlog.md` to nav under Development section (or new "Project Status" section)
- Verify `backup-recovery.md` renders properly after fix

## 6. IMPLEMENTATION_GUIDE.md

Inspect the root-level `IMPLEMENTATION_GUIDE.md` for the same inline status patterns. If present, apply the same cleanup. If clean, skip.

## Success Criteria

1. No domain doc contains multi-paragraph "not implemented" / "SUPERSEDED" / "deferred" blocks
2. Every extracted gap has a tracked entry in open-backlog.md with a stable ID
3. Domain docs use one-line status markers that link to the backlog
4. spec_v3.md has a single §0 status matrix; body reads as clean design prose
5. Historical content is in archive/ directories, accessible but out of the reading path
6. followups.md is <200 lines with only open items
7. All MkDocs internal links resolve
8. No information is lost — everything is either in the clean doc, the backlog, or the archive

## Estimated Effort

| Work Item | Files | Effort |
|-----------|-------|--------|
| Expand open-backlog.md | 1 | Small |
| Domain doc cleanup (11 files) | 11 | Medium |
| spec_v3.md restructure | 1 | Large |
| followups.md cleanup | 1 | Medium |
| backup-recovery.md fix | 1 | Small |
| MkDocs nav update | 1 | Small |
| IMPLEMENTATION_GUIDE.md check | 1 | Small |
| Link verification | All | Small |

## Documentation Score (Pre/Post)

| Category | Before | After (expected) |
|----------|--------|-------------------|
| Writing quality | 9/10 | 9/10 |
| Coverage | 8/10 | 8/10 |
| Accuracy | 7/10 | 9/10 |
| Readability | 6/10 | 9/10 |
| Navigation | 8/10 | 9/10 |
| Maintainability | 7/10 | 8/10 |
| AI-friendliness | 9/10 | 9/10 |
| **Overall** | **78/100** | **88/100** |
