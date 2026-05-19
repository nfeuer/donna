# Documentation Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean all inline hedging/deferred/obsolete callouts from Donna's documentation, extract them to a tracked gap backlog, archive historical content, and restructure spec_v3.md with a consolidated status matrix.

**Architecture:** Surgical edits to ~15 files. Each domain doc gets callouts replaced with one-liner status notes linking to the expanded `open-backlog.md`. `spec_v3.md` gets a new §0 status matrix, inline status blocks removed, and Phase 6 content moved to an appendix. `followups.md` gets closed items archived.

**Tech Stack:** Markdown, MkDocs (properdocs.yml), grep for verification

**Design spec:** `docs/superpowers/specs/2026-05-18-documentation-cleanup-design.md`

---

### Task 1: Scaffold Archive Directories and Expand open-backlog.md

**Files:**
- Create: `docs/domain/archive/.gitkeep`
- Modify: `docs/superpowers/followups/open-backlog.md`

- [ ] **Step 1: Create the archive directory**

```bash
mkdir -p docs/domain/archive
touch docs/domain/archive/.gitkeep
```

- [ ] **Step 2: Read current open-backlog.md**

```bash
cat docs/superpowers/followups/open-backlog.md
```

Understand the current structure: title, date, scope note, priority legend, Triggered section, OOS section, summary table.

- [ ] **Step 3: Expand open-backlog.md with three new gap tiers**

Insert three new sections **before** the existing "Triggered" section (after the `---` on line 8). The new sections hold items extracted from domain docs. Keep all existing content (Triggered, OOS) intact below.

Insert this content after line 8 (`---`):

```markdown

## Gaps extracted from documentation audit (2026-05-18)

Items below were inline callouts in domain docs, now tracked here as the canonical gap list. Each has a stable ID (G-*) referenced from the source doc.

### Critical — blocks a feature

| ID | Feature | Current State | What's Blocking | Source Doc | Spec § |
|----|---------|--------------|-----------------|-----------|--------|
| G-1 | GmailClient wiring | Not wired into orchestrator boot | email_triage automation can't run | domain/agents.md | §12.1 |
| G-2 | SkillSystemConfig runtime wiring | Pydantic model exists, fields not read by runtime | Thresholds hardcoded as module constants | domain/skill-system.md | §23 |

### Partial — shipped with gaps

| ID | Feature | What's Shipped | What's Missing | Source Doc | Spec § |
|----|---------|---------------|---------------|-----------|--------|
| G-10 | Priority escalation | Deadline + workload pressure | Dependency-chain, user-lock flag | domain/task-system.md | §5.5.2 |
| G-11 | Scheduling conflict resolution | Basic overlap detection | Priority displacement, cascade-shift, dual-invite | domain/scheduling.md | §6.2 |
| G-12 | Time windows | 6 of 8 live | Extended Work, Emergency Work not configured | domain/scheduling.md | §6.1.2 |
| G-13 | Observability DB | invocation_log in donna_tasks.db + Loki | Dedicated donna_logs.db not implemented | domain/observability.md | §14.3.1 |
| G-14 | Notification tiers | Discord DM (tier 1-2) | Email tier 3 | domain/notifications.md | §11.1 |
| G-15 | Budget breach handling | Pause-only + escalation decision tree (slices 17-24) | Pause-only path still active as fallback | workflows/handle-budget-breach.md | §18 |
| G-16 | MorningDigest production wiring | Construction code exists | No production call site in orchestrator | domain/management-gui.md | §22 |
| G-17 | Tool gap queue UI | Data model + Discord ping shipped (slice 22) | Standalone dashboard queue surface | domain/management-gui.md | §22 |
| G-18 | Task soft-delete path | MemoryStore.delete() ready | No soft-delete on tasks table or Database API | domain/memory-vault/episodic.md | §30 |

### Deferred / Phase 6 — not started, by design

| ID | Feature | Rationale | Trigger Condition | Spec § |
|----|---------|-----------|-------------------|--------|
| G-20 | MCP Tier 2 (FastMCP) | Only Tier 1 needed currently | User needs GitHub/Notes/SearXNG integration | §3.2 |
| G-21 | Coding Agent | Safety gate: Phase 6 | Code generation use case arises | §7.1.1 |
| G-22 | Communication Agent | Safety gate: Phase 6 | Email/message drafting use case arises | §7.1.1 |
| G-23 | Off-server backup (GCS/Backblaze) | Local NVMe sufficient | Disaster recovery requirement | §16.3.2 |
| G-24 | Flutter app | API shipped, UI in sibling repo | Mobile use case prioritized | §20 |
| G-25 | donna_logs.db dedicated log DB | Loki pipeline works | Need SQLite-queryable structured logs | §14.3.1 |
| G-26 | Per-task-type compaction strategies | Heuristic token estimation sufficient | Context overflow rate > 10% | §4 |
| G-27 | pgvector brain on Supabase | Not needed for current scale | Long-history retrieval required | §4 |
| G-28 | Exact tokenization (Ollama /api/tokenize) | Heuristic sufficient | Token estimation drift causes problems | §4 |
| G-29 | Per-alias daily caps on overflow | No overflow pattern observed | Overflow escalation rate > threshold | §4 |

---
```

- [ ] **Step 4: Update the summary table at the bottom of the file**

Replace the existing summary table with:

```markdown
## Summary table

| Bucket | Items | Priority |
|---|---|---|
| Critical gaps | 2 items (G-1, G-2) | P1 |
| Partial implementations | 9 items (G-10 – G-18) | P2 |
| Deferred / Phase 6 | 10 items (G-20 – G-29) | P2 |
| Triggered (deferred) | 9 items | P2 |
| OOS (triggered by spec) | 12 items | P2 |

Historical closed items (Waves 1–5): see the archived tracker.
```

- [ ] **Step 5: Verify and commit**

```bash
# Verify the file parses correctly
grep -c "^| G-" docs/superpowers/followups/open-backlog.md
# Expected: 21 rows (2 critical + 9 partial + 10 deferred)

git add docs/domain/archive/.gitkeep docs/superpowers/followups/open-backlog.md
git commit -m "docs: expand open-backlog.md with gap tracker tiers from doc audit"
```

---

### Task 2: Clean Up skill-system.md

**Files:**
- Modify: `docs/domain/skill-system.md`
- Create: `docs/domain/archive/skill-system-history.md`

This is the largest cleanup — 5 separate edits to an 865-line file.

- [ ] **Step 1: Read skill-system.md**

```bash
cat -n docs/domain/skill-system.md
```

Identify the exact line ranges for all five callout blocks described below.

- [ ] **Step 2: Replace the "SkillSystemConfig dead code" callout (around lines 84–90)**

Find the block that starts with:
```
> **Heads up — `SkillSystemConfig` is currently dead code.**
```
This is a multi-line blockquote explaining that the Pydantic model exists but fields aren't read. Replace the entire blockquote (which may span ~6-10 lines, ending before the next heading or paragraph) with:

```markdown
> `SkillSystemConfig` fields are loaded at startup via `cli_wiring.py`. Hardcoded thresholds in matcher/registry serve as fallbacks. *Full wiring tracked as [G-2](../superpowers/followups/open-backlog.md).*
```

- [ ] **Step 3: Archive the SUPERSEDED §4.1–4.2 sections (around lines 98–179)**

Find the block starting with:
```
> **⚠️ SUPERSEDED (2026-04-21):**
```
This warning plus the two manual-wiring subsections (§4.1 and §4.2) that follow it need to be archived. 

a) Copy the SUPERSEDED warning and both §4.1/§4.2 sections to a new file `docs/domain/archive/skill-system-history.md`:

```markdown
# Skill System — Historical Manual Wiring

> Archived 2026-05-18. These manual steps were superseded on 2026-04-21 by
> `wire_skill_system()` and `assemble_skill_system()` in `src/donna/cli_wiring.py`.

## Former §4.1 and §4.2 — Manual Wiring Steps

<paste the full content of the superseded sections here>
```

b) Replace the entire block in skill-system.md (from the ⚠️ warning through the end of §4.2) with:

```markdown
> Phase 1–2 wiring is fully automated by `assemble_skill_system()` in `src/donna/cli_wiring.py:300-470`, invoked from the CLI startup path. Historical manual steps archived in [skill-system-history.md](archive/skill-system-history.md).
```

- [ ] **Step 4: Clean up "What's NOT active yet" section (around lines 254–261)**

Find the section headed `## 7. What's NOT active yet` (or similar heading). This section lists Phase 3–5 features as "not active" even though they're documented in detail in the Phase 3–5 sections later in the same file.

Replace the entire section (heading + bullet list) with:

```markdown
## 7. Phase 3–5 Feature Status

All Phase 3–5 features (shadow sampling, lifecycle transitions, auto-drafting, evolution loop, automation subsystem) are documented in their respective sections below. Current activation status is tracked in the [open backlog](../superpowers/followups/open-backlog.md).
```

- [ ] **Step 5: Clean up "P4.5 Deferred items" section (around lines 557–560)**

Find the section headed `### P4.5 Deferred items`. These are actually design constraints, not deferred work.

Replace the heading:
```markdown
### P4.5 Design Constraints
```

In the body text, remove any language suggesting these are temporary ("deferred"). Keep the factual description of how sandbox executor validation works and that human approval is required for `draft → sandbox`.

- [ ] **Step 6: Clean up "P5.7 Deferred" section (around lines 621–627)**

Find the section headed `### P5.7 Deferred`. Replace heading and body:

```markdown
### P5.7 Planned Enhancements

Event-triggered automations (OOS-1), automation composition (OOS-3), cross-user sharing (OOS-7), dashboard UI, and Discord natural-language creation are tracked in the [open backlog](../superpowers/followups/open-backlog.md).
```

- [ ] **Step 7: Verify no remaining hedging language**

```bash
grep -n -i "not yet\|dead code\|SUPERSEDED\|deferred\|not active\|dormant\|placeholder\|stub" docs/domain/skill-system.md
```

Review each remaining hit. Some uses of "deferred" in OOS references are acceptable (they link to the backlog). Remove any multi-paragraph explanations that remain.

- [ ] **Step 8: Commit**

```bash
git add docs/domain/skill-system.md docs/domain/archive/skill-system-history.md
git commit -m "docs: clean up skill-system.md — archive superseded sections, extract gaps to backlog"
```

---

### Task 3: Clean Up agents.md, cost.md, slices.md

**Files:**
- Modify: `docs/domain/agents.md`
- Modify: `docs/domain/cost.md`
- Modify: `docs/development/slices.md`

Three small, independent fixes grouped into one task.

- [ ] **Step 1: Read all three files**

```bash
cat -n docs/domain/agents.md
cat -n docs/domain/cost.md
cat -n docs/development/slices.md
```

- [ ] **Step 2: Clean agents.md — unimplemented agent rows (around lines 18–19)**

Find the two table rows for Coding and Communication agents that end with `**Not yet implemented.**`. Replace the trailing status text:

For the Coding row, change:
```
**Not yet implemented.**
```
to:
```
*Phase 6 — [G-21](../superpowers/followups/open-backlog.md)*
```

For the Communication row, change:
```
**Not yet implemented.**
```
to:
```
*Phase 6 — [G-22](../superpowers/followups/open-backlog.md)*
```

Keep the rest of each row (description, tool access, autonomy level) intact — the design constraints are valuable context.

- [ ] **Step 3: Fix cost.md — remove copy-paste error (around lines 96–98)**

Find the block that starts with:
```
> **⚠️ SUPERSEDED (2026-04-21):** §4.1 and §4.2 below describe manual wiring
```

This text is from skill-system.md and does not belong in cost.md (cost.md has no §4.1 or §4.2). Delete the entire blockquote (typically 3-5 lines starting with `>`).

Also find any "deferred to slice 24" callout about dependent-skill regression testing. If it's a multi-line block, replace with:
```markdown
*Dependent-skill regression testing deferred — [open backlog](../superpowers/followups/open-backlog.md).*
```

- [ ] **Step 4: Update slices.md — remove "in flight" marker (around line 25)**

Find the line:
```
| 15 | Template writes + meeting notes — *brief only (in flight)* |
```
Replace with:
```
| 15 | Template writes + meeting notes |
```

- [ ] **Step 5: Verify and commit**

```bash
grep -n "Not yet implemented" docs/domain/agents.md
# Expected: 0 matches
grep -n "SUPERSEDED" docs/domain/cost.md
# Expected: 0 matches
grep -n "in flight" docs/development/slices.md
# Expected: 0 matches

git add docs/domain/agents.md docs/domain/cost.md docs/development/slices.md
git commit -m "docs: clean agents.md (phase tags), cost.md (remove copy-paste error), slices.md (remove stale marker)"
```

---

### Task 4: Clean Up observability.md and model-layer.md

**Files:**
- Modify: `docs/domain/observability.md`
- Modify: `docs/domain/model-layer.md`

- [ ] **Step 1: Read both files**

```bash
cat -n docs/domain/observability.md
cat -n docs/domain/model-layer.md
```

- [ ] **Step 2: Clean observability.md — aspirational logging DB section (around lines 64–66)**

Find the section headed `## Logging Database (aspirational)` or similar. Replace the heading:
```markdown
## Logging Database
```

Find the multi-line status blockquote starting with `> **Status:** The dedicated donna_logs.db described below is a design target...`. Replace the entire blockquote with:

```markdown
> Logging uses structlog → stdout → Docker json-file → Promtail → Loki. LLM invocation tracking lives in the `invocation_log` table in `donna_tasks.db`. The dedicated `donna_logs.db` design below is retained as a reference for future optimization. *Tracked as [G-13, G-25](../superpowers/followups/open-backlog.md).*
```

- [ ] **Step 3: Clean observability.md — duplicate "not yet implemented" note (around line 125)**

Find the note block starting with `> **Note:** Step 4 in the original spec described a lightweight log collector...`. Delete the entire note — it's now covered by the updated section header from step 2.

- [ ] **Step 4: Clean model-layer.md — deferred extensions section (around lines 233–240)**

Find the section headed `### Future extensions (explicitly deferred)` or similar. Replace heading and body:

```markdown
### Future Enhancements

Documented in [`archive/2026-04-12-local-llm-context-strategy-design.md`](../superpowers/specs/archive/2026-04-12-local-llm-context-strategy-design.md):
per-task-type compaction strategies, `pgvector` brain on Supabase, exact tokenization via Ollama, and per-alias daily caps on overflow escalations. *Tracked as [G-26 – G-29](../superpowers/followups/open-backlog.md).*
```

- [ ] **Step 5: Verify and commit**

```bash
grep -n -i "not yet implemented\|aspirational" docs/domain/observability.md
# Expected: 0 matches
grep -n -i "explicitly deferred" docs/domain/model-layer.md
# Expected: 0 matches

git add docs/domain/observability.md docs/domain/model-layer.md
git commit -m "docs: clean observability.md (remove aspirational framing) and model-layer.md (clean deferred section)"
```

---

### Task 5: Clean Up memory-vault.md, notifications.md, management-gui.md

**Files:**
- Modify: `docs/domain/memory-vault.md`
- Modify: `docs/domain/notifications.md`
- Modify: `docs/domain/management-gui.md`

- [ ] **Step 1: Read all three files**

```bash
cat -n docs/domain/memory-vault.md
cat -n docs/domain/notifications.md
cat -n docs/domain/management-gui.md
```

- [ ] **Step 2: Clean memory-vault.md — dormant code branch (around line 130)**

Find the text containing `**dormant in production**` about the soft-delete path on the tasks table. This is within a paragraph about delete events. Replace the sentence containing "dormant in production" with:

```markdown
The delete-event handler is tested but awaits a soft-delete path on the `tasks` table. *Tracked as [G-18](../superpowers/followups/open-backlog.md).*
```

Also scan for other deferred-slice callouts in the file (slice 14, 15, 16 references). For any multi-line "not yet implemented" blocks, replace with a one-liner linking to the backlog. Single-line slice references like "added in slice 14" are fine — they're factual provenance, not hedging.

- [ ] **Step 3: Clean notifications.md — email tier 3 deferred (around line 228)**

Find the table row:
```
| 3 | Email | Deferred to slice 8 |
```
Replace with:
```
| 3 | Email | *Planned — [G-14](../superpowers/followups/open-backlog.md)* |
```

- [ ] **Step 4: Clean management-gui.md — tool gap queue deferred (around lines 492–499)**

Find the paragraph about "Tool gap queue (future)" containing "not yet scheduled". Replace the paragraph:

```markdown
3. **Tool gap queue** — data model and Discord ping shipped in slice 22. Standalone dashboard queue surface tracked as [G-17](../superpowers/followups/open-backlog.md). Currently visible as escalation-workspace rows of type `tool_request_fulfillment` and as dashboard-setting audit entries in `/admin/logs`.
```

- [ ] **Step 5: Verify and commit**

```bash
grep -n "dormant in production" docs/domain/memory-vault.md
# Expected: 0 matches
grep -n "Deferred to slice" docs/domain/notifications.md
# Expected: 0 matches
grep -n "not yet scheduled" docs/domain/management-gui.md
# Expected: 0 matches

git add docs/domain/memory-vault.md docs/domain/notifications.md docs/domain/management-gui.md
git commit -m "docs: clean memory-vault, notifications, management-gui — extract gaps to backlog"
```

---

### Task 6: Clean Up handle-budget-breach.md and backup-recovery.md

**Files:**
- Modify: `docs/workflows/handle-budget-breach.md`
- Modify: `docs/operations/backup-recovery.md`

- [ ] **Step 1: Read both files**

```bash
cat -n docs/workflows/handle-budget-breach.md
cat -n docs/operations/backup-recovery.md
```

- [ ] **Step 2: Clean handle-budget-breach.md — remove "Roadmap" temporary marker (around lines 6–14)**

Find the blockquote starting with `> **Roadmap:** the pause-only behavior described below is being replaced...`. This multi-line block marks the entire page as temporary. Replace it with:

```markdown
> The escalation decision tree (Approve / Manual / Pause / Cancel) is implemented in slices 17–24. This page documents the budget-guard behavior that triggers escalation. For the full decision tree, see [Manual Escalation](../superpowers/specs/manual-escalation.md) and [Cost](../domain/cost.md).
```

- [ ] **Step 3: Clean handle-budget-breach.md — remove "rows will be updated" language**

Search the rest of the file for any remaining "this page reflects current behavior until those slices land" or "rows will be updated per-slice" language. Remove those sentences.

- [ ] **Step 4: Fix backup-recovery.md**

The file is a stub that embeds `RECOVERY.md` via MkDocs snippet syntax. This is actually correct behavior for MkDocs — the `--8<--` directive inlines the file at build time. Add a brief intro paragraph after the title:

```markdown
# Backup & Recovery

Procedures for database backup, disaster recovery, and data restoration. The canonical source is `RECOVERY.md` at the repo root; it is embedded below for the docs site.
```

Keep the existing snippet and related links.

- [ ] **Step 5: Verify and commit**

```bash
grep -n "Roadmap\|rows will be updated\|reflects current behavior" docs/workflows/handle-budget-breach.md
# Expected: 0 matches

git add docs/workflows/handle-budget-breach.md docs/operations/backup-recovery.md
git commit -m "docs: clean handle-budget-breach.md (remove temp markers), fix backup-recovery.md intro"
```

---

### Task 7: spec_v3.md — Add §0 Implementation Status Matrix

**Files:**
- Modify: `spec_v3.md`

This is the first of three tasks for spec_v3.md. This task only adds content; tasks 8 and 9 remove/move content.

- [ ] **Step 1: Read the top of spec_v3.md to find the insertion point**

```bash
head -80 spec_v3.md
```

Find the end of the "Revision Notes (v3.1, April 2026)" section. The §0 matrix goes immediately after the revision notes and before §1 (Executive Summary).

- [ ] **Step 2: Insert the §0 Implementation Status Matrix**

After the last line of the revision notes section (before the line starting `**1. Executive Summary**` or `## 1`), insert:

```markdown

---

## §0 Implementation Status (v3.1, April 2026)

This matrix summarizes implementation state for every spec section. Sections not listed are fully shipped as specified. Backlog IDs reference [`open-backlog.md`](docs/superpowers/followups/open-backlog.md).

| Section | Status | Notes | Backlog |
|---------|--------|-------|---------|
| §3.2–3.3 MCP Strategy | Deferred | Tier 1 (direct Python) only. Tier 2 (FastMCP) deferred to Phase 6. | G-20 |
| §5.5.2 Priority Escalation | Partial | Deadline + workload live. Dependency-chain and user-lock flag not built. | G-10 |
| §6.1.2 Conflict Resolution | Partial | 2 of 5 strategies implemented (overlap detect, user-reschedule). | G-11 |
| §6.2 Time Constraints | Partial | 6 of 8 windows live. Extended Work and Emergency Work spec-only. | G-12 |
| §7.1.1 Coding Agent | Defined, disabled | Phase 6 gate — Stage 3 tools + MCP required. | G-21 |
| §7.1.1 Communication Agent | Defined, disabled | Phase 6 gate — Stage 3 tools + MCP required. | G-22 |
| §11.1 Notifications | Partial | Discord DM (tiers 1–2). Email tier 3 not implemented. | G-14 |
| §12.1 Integration Matrix | Partial | Gmail, Calendar, Discord, Twilio, Supabase, SQLite live. GitHub, Filesystem, Notes, SearXNG deferred. | G-20 |
| §14.3 Logging | Changed | `invocation_log` in `donna_tasks.db` + Loki. No standalone `donna_logs.db`. | G-25 |
| §16.3.2 Backup | Partial | Local NVMe backups only. Off-server push (GCS/Backblaze) not built. | G-23 |
| §20 Phases | 1–5 complete | Phase 6 content moved to appendix. | — |
| §23.3 Tool Registry | Partial | Read tools live. Write tools (`task_db_write`, `calendar_write`) not in registry. | — |
| §30 Memory & Vault | Partial | Slices 12–15 shipped. Re-rendering, Supabase sync, BM25 hybrid deferred. | G-18 |

---
```

- [ ] **Step 3: Verify the matrix was inserted correctly**

```bash
grep -n "§0 Implementation Status" spec_v3.md
# Expected: 1 match at the insertion point
grep -c "^|" spec_v3.md | head -1
# Count table rows to verify nothing was corrupted
```

- [ ] **Step 4: Commit**

```bash
git add spec_v3.md
git commit -m "docs(spec): add §0 implementation status matrix to spec_v3.md"
```

---

### Task 8: spec_v3.md — Remove Inline Status Blocks

**Files:**
- Modify: `spec_v3.md`

Remove all 5 "v3.1 Implementation Status" blockquotes from the spec body. The §0 matrix (added in Task 7) is now the single source of truth.

- [ ] **Step 1: Find all 5 status blocks**

```bash
grep -n "v3.1 Implementation Status" spec_v3.md
```

Expected locations (approximate — verify exact lines after Task 7's insertion shifted them):
1. ~Line 240–247: §3.2 MCP Integration
2. ~Line 1765–1772: §6.1.2 Conflict Resolution
3. ~Line 1810–1815: §6.2 Time Constraints
4. ~Line 1914–1920: §7.1.1 Agent Hierarchy
5. ~Line 2607–2613: §14.3 Logging Database

- [ ] **Step 2: Remove each status block**

For each of the 5 blocks: delete the entire blockquote (lines starting with `>` that contain "v3.1 Implementation Status" and any continuation lines). The surrounding spec content stays intact.

Each block follows this pattern:
```
> **v3.1 Implementation Status:** <multi-line text>
```

Delete the block. Leave no blank line gap larger than 2 lines (clean up extra whitespace).

- [ ] **Step 3: Remove standalone deferred markers**

Search for these additional inline markers and remove or shorten them:

```bash
grep -n "not yet implemented in the registry\|re-runs are deferred to slice\|are \*\*not yet built\*\*\|Still deferred:" spec_v3.md
```

For each hit:
- If it's a standalone sentence about implementation status, delete it.
- If it's embedded in a factual paragraph, trim to just the factual content without the status commentary.
- If it references a backlog item, replace with a link: `*(see [open-backlog.md](docs/superpowers/followups/open-backlog.md))*`

- [ ] **Step 4: Verify all inline status blocks are gone**

```bash
grep -c "v3.1 Implementation Status" spec_v3.md
# Expected: 0
grep -n "not yet implemented\|not yet built\|Still deferred" spec_v3.md
# Review each remaining hit — some may be acceptable in the revision notes section
```

- [ ] **Step 5: Commit**

```bash
git add spec_v3.md
git commit -m "docs(spec): remove inline v3.1 status blocks — §0 matrix is now single source of truth"
```

---

### Task 9: spec_v3.md — Move Phase 6 Content to Appendix

**Files:**
- Modify: `spec_v3.md`

- [ ] **Step 1: Find the Phase 6 section**

```bash
grep -n "Phase 6" spec_v3.md
```

Find the Phase 6 roadmap section (around line 3530–3582). Also identify any detailed subsections about unbuilt Phase 6 features scattered elsewhere in the spec.

- [ ] **Step 2: Read the Phase 6 section and revision notes**

Read the Phase 6 section fully. Also re-read the revision notes (top of file) to understand what's already summarized there vs. what's only in the Phase 6 section.

- [ ] **Step 3: Create the appendix at the end of the file**

At the very end of spec_v3.md, add:

```markdown

---

## Appendix: Phase 6 — Future Design

> The following content describes planned features that are not yet implemented.
> It is preserved as design intent for when implementation begins. See the
> [§0 status matrix](#0-implementation-status-v31-april-2026) for current state.

### A.1 Phase 6 Roadmap

<move the Phase 6 section content here>

### A.2 MCP Tier 2 Integration Details

<if there are detailed MCP Tier 2 subsections elsewhere in the spec, move them here>

### A.3 Coding Agent Design

<if there are detailed Coding Agent subsections beyond the hierarchy table, move them here>

### A.4 Communication Agent Design

<if there are detailed Communication Agent subsections beyond the hierarchy table, move them here>
```

Only move content that is *entirely* about unbuilt features. The agent hierarchy table (§7.1.1) should stay in place — it documents both live and disabled agents. The MCP architecture overview (§3.2) should stay — it's the design blueprint. Only move *detailed implementation plans* for Phase 6 features.

- [ ] **Step 4: Add pointers at original locations**

At each location where content was moved, insert a one-line pointer:

```markdown
*Detailed Phase 6 implementation plan: see [Appendix A.1](#a1-phase-6-roadmap).*
```

- [ ] **Step 5: Verify structure**

```bash
grep -n "Appendix.*Phase 6" spec_v3.md
# Should find the appendix heading
grep -n "See.*Appendix" spec_v3.md
# Should find pointers from original locations
```

- [ ] **Step 6: Commit**

```bash
git add spec_v3.md
git commit -m "docs(spec): move Phase 6 detailed plans to appendix, add pointers from original sections"
```

---

### Task 10: Clean Up followups.md

**Files:**
- Modify: `docs/superpowers/specs/followups.md`
- Create: `docs/superpowers/specs/archive/followups-closed-slices.md`

The file is 1,047 lines. 28 items are closed; 15 are open. Move closed items to archive, keep open items.

- [ ] **Step 1: Read followups.md fully**

```bash
cat -n docs/superpowers/specs/followups.md
```

Identify each slice section and which items within it are CLOSED (marked `resolved-in-*` or `wontfix`) vs OPEN.

- [ ] **Step 2: Create the archive file**

Create `docs/superpowers/specs/archive/followups-closed-slices.md` with all closed items:

```markdown
# Followups — Closed Items Archive

> Archived 2026-05-18. These items were resolved during slices 18–24.
> Open items remain in [`followups.md`](../followups.md).

<paste all closed items here, organized by slice, preserving their original formatting>
```

- [ ] **Step 3: Rewrite followups.md with only open items**

Rewrite the file to contain only the 15 open items, organized by slice. Each slice section should have a heading and only its open items. If a slice has zero open items, omit it entirely.

Expected structure:
```markdown
# Spec Follow-ups — Open Items

> Gaps, drifts, and deferred decisions across the slice-driven build.
> Closed items archived in [`archive/followups-closed-slices.md`](archive/followups-closed-slices.md).
> Canonical gap tracker: [`open-backlog.md`](../../superpowers/followups/open-backlog.md).

## S18 — Budget Extension
<1 open item: crash-recovery resume>

## S19 — Dashboard Escalation Workspace
<1 open item: mode/resolution duplication>

## S20 — Chat Mode
<2 open items: textarea pre-fill, conversation-engine estimate threading>

## S20-FU — Chat Mode Follow-ups
<1 open item: summarizer template caching>

## S21 — Claude Code Mode
<1 open item: re-escalation depth limit>

## S22 — Tool Gap Surfacing
<5 open items: validation depth, requires_rebuild nag, iteration-cap orphan, warnings field, MorningDigest wiring>

## S24 — Escalation Hardening (Audit Residue)
<4 open items: dependent-skill regression, depth limit, Twilio E2E, re-estimate>

## Standalone Feature Follow-ups
<4 open items: Discord DM, tz threading, calendar IN_PROGRESS, event-driven corrections>
```

- [ ] **Step 4: Verify line count**

```bash
wc -l docs/superpowers/specs/followups.md
# Target: < 200 lines
```

If over 200 lines, trim the per-item detail (keep one-liner summary + link to archived detail).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/followups.md docs/superpowers/specs/archive/followups-closed-slices.md
git commit -m "docs: archive 28 closed followup items, trim followups.md to open items only"
```

---

### Task 11: Update MkDocs Nav and Verify Links

**Files:**
- Modify: `properdocs.yml`

- [ ] **Step 1: Read properdocs.yml**

```bash
cat -n properdocs.yml
```

- [ ] **Step 2: Verify archive directories are excluded from nav**

Check if the `exclude_docs` config or `awesome-pages` plugin already handles excluding `archive/` directories. The superpowers directory is already excluded (`exclude_docs: superpowers/`). Check if `domain/archive/` needs explicit exclusion.

If `domain/archive/` would appear in the nav, add it to the exclude pattern:
```yaml
- awesome-pages:
    # ...existing config...
```

Or if using `exclude_docs`, add the pattern.

- [ ] **Step 3: Verify IMPLEMENTATION_GUIDE.md is clean**

```bash
grep -n -i "not yet implemented\|SUPERSEDED\|dead code\|deferred to slice\|not yet built" IMPLEMENTATION_GUIDE.md
```

Research showed no inline status patterns in this file — verify that's still the case. If any are found, apply the same cleanup pattern (replace with one-liner linking to open-backlog.md).

- [ ] **Step 4: Verify no broken internal links**

```bash
# Check for links to files that should still exist
grep -rn "\[.*\](.*\.md)" docs/ --include="*.md" | grep -v "archive\|superpowers/specs\|superpowers/plans" | head -50
```

Specifically verify these links work after the cleanup:
- `open-backlog.md` links from domain docs use correct relative paths
- `archive/skill-system-history.md` link from skill-system.md
- `archive/followups-closed-slices.md` link from followups.md

- [ ] **Step 5: Build the docs site (if mkdocs is installed)**

```bash
# Only if mkdocs is available
pip install -e ".[docs]" 2>/dev/null && properdocs build --strict 2>&1 | tail -20
```

If the build fails, fix any broken links or missing pages.

- [ ] **Step 6: Commit any nav changes**

```bash
# Only if properdocs.yml was modified
git add properdocs.yml
git commit -m "docs: update MkDocs nav to exclude archive directories"
```

---

### Task 12: Final Verification and Changelog

**Files:**
- Modify: `docs/changelog.md`

- [ ] **Step 1: Run full hedging-language scan across all docs**

```bash
grep -rn -i "not yet implemented\|not yet wired\|not yet active\|SUPERSEDED\|dead code\|dormant in production\|deferred to slice\|not yet scheduled\|in flight\|not yet built" docs/domain/ docs/workflows/ docs/operations/ docs/development/ docs/architecture/ --include="*.md" | grep -v "archive/"
```

Every remaining hit should be either:
- A one-liner status note linking to open-backlog.md
- A factual reference (e.g., "added in slice 14") that's provenance, not hedging

If any multi-paragraph callouts remain, fix them.

- [ ] **Step 2: Run the same scan on spec_v3.md**

```bash
grep -n "v3.1 Implementation Status\|not yet implemented\|not yet built\|Still deferred" spec_v3.md
```

Only acceptable hits are in the §0 status matrix or revision notes.

- [ ] **Step 3: Verify followups.md length**

```bash
wc -l docs/superpowers/specs/followups.md
# Must be < 200 lines
```

- [ ] **Step 4: Update changelog.md**

Add an entry at the top of the changelog:

```markdown
## 2026-05-18

### Documentation Cleanup
- Extracted all inline "not implemented" / "deferred" / "obsolete" callouts from 11 domain docs into [`open-backlog.md`](superpowers/followups/open-backlog.md) with stable gap IDs (G-1 through G-29)
- Added §0 Implementation Status Matrix to `spec_v3.md` — single source of truth for what's shipped vs. deferred
- Removed 5 inline "v3.1 Implementation Status" blocks from spec body
- Moved Phase 6 detailed plans to spec appendix
- Archived closed followup items (28 items); trimmed `followups.md` to open items only
- Fixed copy-paste error in `cost.md` (had skill-system.md content)
- Fixed `backup-recovery.md` stub with proper intro
- Archived superseded manual wiring steps from `skill-system.md`
- See [design spec](superpowers/specs/2026-05-18-documentation-cleanup-design.md)
```

- [ ] **Step 5: Final commit**

```bash
git add docs/changelog.md
git commit -m "docs: add documentation cleanup to changelog"
```
