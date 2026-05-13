# Prompts Page Redesign

**Date:** 2026-05-12
**Status:** Approved
**Spec ref:** spec_v3.md (no section affected — UI-only change)

## Problem

The current Prompts page has two UX issues:

1. **Flat list with wasted space.** Prompts are rendered as a single vertical list of filenames with no grouping or visual hierarchy. The list uses minimal horizontal space, leaving the rest of the page blank.
2. **Editor below the list.** When a prompt is selected, the editor appears below the full file list, forcing the user to scroll past all 27 entries to reach it. The list doesn't collapse or move aside.

## Solution

Redesign to a **sidebar + main panel** layout matching the existing Logs page pattern. The sidebar contains a grouped, collapsible file tree. The main panel shows either a stats welcome view (when no prompt is selected) or the existing editor (when a prompt is selected).

## Layout

### Grid structure

```
┌──────────┬──────────────────────────────────┐
│ Sidebar  │  Main panel                      │
│ (260px)  │  (flex: 1)                       │
│ sticky   │                                  │
│ scroll-y │                                  │
└──────────┴──────────────────────────────────┘
```

- Root element: `display: grid; grid-template-columns: 260px 1fr;`
- Sidebar: `position: sticky; top: var(--space-4);` with independent `overflow-y: auto` and `max-height: calc(100vh - var(--space-6))`.
- Responsive: at `max-width: 900px`, collapse to single column with sidebar limited to 220px height.
- Uses the same CSS variable vocabulary and border/radius/surface tokens as `Logs.module.css`.

### Sidebar content

1. **Search input** at the top — filters the file list by substring match on filename.
2. **Folder groups** — collapsible sections for each subdirectory (CHAT, ESCALATION, SKILLS, VAULT), with item count badges. Same expand/collapse chevron pattern as `EventTypeTree`.
3. **Root prompts** — listed below a visual separator, since they have no parent folder.
4. **File items** — name only (no `.md` extension). Active item highlighted with `border-left: 2px solid var(--color-accent)` and `background: var(--color-accent-soft)`.

No metadata (size, date) in the sidebar — keeps it narrow and scannable. Metadata appears in the main panel.

### Main panel — empty state (no prompt selected)

A welcome dashboard with stat cards, using the same `Card`, `Stat`, `Pill`, `ChartCard`, and eyebrow label patterns as the main Dashboard page. Includes staggered `cardRise` fade-in animation on mount (matching `Dashboard.module.css`).

**Stat sections (displayed as cards in a 2-column grid):**

1. **Overview card** — `ChartCard`-style with:
   - Eyebrow: "Prompt Templates"
   - Headline metric: total prompt count (e.g. "27")
   - Stat strip: breakdown by folder (e.g. "5 chat · 4 escalation · 3 skills · 15 root")

2. **Most invoked card** — Top 5 prompts by invocation count:
   - Ranked list with prompt name (mono font), invocation count, and total cost.
   - Data source: `invocation_log` joined with `task_types.yaml` prompt_template paths.
   - Clickable rows — clicking navigates to that prompt in the sidebar/editor.

3. **Agent coverage card** — Prompts ranked by how many agents reference them:
   - Each row shows prompt name + agent name pills (using `Pill variant="muted"`).
   - Data source: `agents.yaml` → `task_types.yaml` mapping (same as `admin_agents.py` `_build_agent_task_map`).

4. **Local vs Cloud card** — Distribution of prompts by model routing:
   - Horizontal `BarChart` or simple stat with counts: how many prompts route to `local_parser` (Ollama, zero marginal cost) vs `parser`/`reasoner` (Claude API, paid).
   - Data source: `task_types.yaml` → `donna_models.yaml` routing.

5. **Recently modified + Unused card** — Two sub-sections:
   - Last 3 recently modified prompts with relative timestamps. Clickable.
   - Prompts with zero invocations, if any. Useful for spotting orphaned or untriggered prompts.

### Main panel — prompt selected

Same as the current `PromptEditor` component, minus the `PromptFileList` (that's now in the sidebar). Layout:

1. **Header row**: filename (mono) + Save button + Variables button.
2. **Metadata bar**: file size, modified date, linked model pill, linked schema pill.
3. **Tab bar**: Edit / Preview / Split.
4. **Editor**: Monaco editor (existing).
5. **Variable inspector**: existing component, below editor.

## Backend

### New endpoint: `GET /admin/prompts/stats`

Returns all data needed for the welcome card in a single request. Response shape:

```json
{
  "total": 27,
  "by_folder": { "chat": 5, "escalation": 4, "skills": 3, "root": 15 },
  "most_invoked": [
    { "prompt": "parse_task.md", "task_type": "parse_task", "invocations": 342, "cost_usd": 1.23 }
  ],
  "agent_coverage": [
    { "prompt": "parse_task.md", "agents": ["pm"] }
  ],
  "model_routing": {
    "local_parser": 8,
    "parser": 10,
    "reasoner": 9
  },
  "recently_modified": [
    { "name": "parse_task.md", "modified": 1715200000 }
  ],
  "unused": ["claude_novelty.md"]
}
```

Implementation: added to `admin_config.py` router. Reads `task_types.yaml` for prompt→task_type→schema mappings, `donna_models.yaml` for model routing, `agents.yaml` for agent→task_type mapping, and queries `invocation_log` for invocation counts/cost per task_type.

### Metadata enrichment on `GET /admin/prompts/{filename:path}`

Add optional fields to the existing prompt detail response:

```json
{
  "name": "parse_task.md",
  "content": "...",
  "size_bytes": 2800,
  "modified": 1715200000,
  "task_type": "parse_task",
  "model_alias": "parser",
  "output_schema": "schemas/task_parse_output.json"
}
```

Derived by reverse-lookup from `task_types.yaml`: find which task_type references this prompt_template path, then pull model and schema from that entry. Returns `null` for fields when no task_type maps to the prompt.

## Frontend file changes

### New files
- `donna-ui/src/pages/Prompts/PromptSidebar.tsx` — grouped file tree with search
- `donna-ui/src/pages/Prompts/PromptSidebar.module.css`
- `donna-ui/src/pages/Prompts/PromptWelcome.tsx` — empty-state stats dashboard
- `donna-ui/src/pages/Prompts/PromptWelcome.module.css`
- `donna-ui/src/api/promptStats.ts` — `fetchPromptStats()` API client

### Modified files
- `donna-ui/src/pages/Prompts/index.tsx` — new sidebar+main grid layout, route handling
- `donna-ui/src/pages/Prompts/Prompts.module.css` — replace vertical flex with grid layout
- `donna-ui/src/pages/Prompts/PromptEditor.tsx` — remove embedded `PromptFileList`; accept `file` prop (already done); add metadata bar with model/schema pills
- `donna-ui/src/App.tsx` — no change needed (wildcard route already in place)

### Deleted files
- `donna-ui/src/pages/Prompts/PromptsList.tsx` — replaced by sidebar
- `donna-ui/src/pages/Prompts/PromptFileList.tsx` — replaced by sidebar

## Design consistency requirements

All visual patterns must match the existing Dashboard and Logs pages:

- **Cards**: use `Card` primitive with `ChartCard` for stat cards that have headline metrics.
- **Stat strips**: use `Stat` primitive (eyebrow + value + optional suffix).
- **Pills**: use `Pill` with appropriate variants (`muted` for labels, `accent` for models, `success`/`warning`/`error` for status).
- **Eyebrow labels**: `font-size: var(--text-eyebrow); letter-spacing: var(--tracking-eyebrow); text-transform: uppercase; color: var(--color-text-muted)`.
- **Section titles**: `font-family: var(--font-display); font-weight: 300; font-size: var(--text-section)`.
- **Mono font for code-like content**: prompt names, variable names, schema paths use `var(--font-mono)`.
- **Entry animation**: staggered `cardRise` keyframe on mount (same as Dashboard).
- **Skeleton loading**: `Skeleton` primitives matching card layout during fetch.
- **Sidebar**: matches `Logs.module.css` sidebar styling — same surface, border, radius, sticky behavior.
- **Colors**: exclusively from `tokens.css` custom properties. No hardcoded hex values.
- **Spacing**: `var(--space-N)` tokens only.
- **Motion**: `var(--duration-fast)`, `var(--duration-base)` with `var(--ease-out)`. Respect `prefers-reduced-motion`.

## Out of scope

- Prompt creation (new file) from the UI.
- Prompt deletion from the UI.
- Drag-and-drop reordering.
- Diff history or version control integration.
