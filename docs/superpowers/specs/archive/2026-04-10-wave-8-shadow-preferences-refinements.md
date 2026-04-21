# Wave 8 — Shadow + Preferences · Design Refinements

> Addendum to the master redesign spec (`docs/superpowers/specs/archive/2026-04-08-donna-ui-redesign-design.md`, lines 422–436). This document captures decisions made during the brainstorming session on 2026-04-09/10 that refine the high-level spec into an implementable shape.

---

## Decisions

### 1. Page structure — split the difference (both pages)

Neither page follows the spec literally nor preserves the current AntD shape. Both adopt a **one scrollable page, no tabs** layout:

- Tabs removed. Data that was behind tabs becomes vertically stacked sections on a single page.
- Stat cards removed. Key numbers fold into chart subtitles (Shadow) or section headers (Preferences).
- Filters preserved, positioned in the `PageHeader` right-aligned area.

### 2. Shadow page layout

Top to bottom:

1. **`PageHeader`** — title "Shadow", subtitle "Evaluation comparisons", right-aligned filters (task type `Select`, days `Select`, `RefreshButton`).
2. **Two `ChartCard`s** in a 2-column grid:
   - Quality Δ over time — subtitle absorbs Wins / Losses / Ties counts.
   - Cost delta over time — subtitle absorbs cost-saved number.
3. **Comparisons section** — section header with count, `DataTable` with keyboard row nav, row click opens `ComparisonDrawer`.
4. **Spot Checks section** — section header with count, `DataTable` with its own pagination footer, no row drawer.

**`ComparisonDrawer`** (new component):
- Header: task type + timestamp + win/loss/tie `Pill`.
- Original input / prompt (scrollable code block).
- Two side-by-side panels: Primary output and Shadow output (scrollable, monospace).
- Metadata row: primary model, shadow model, latency, cost, quality Δ.

**Data fetching:** unchanged — `fetchShadowComparisons`, `fetchShadowStats`, `fetchSpotChecks`.

**Chart theming:** reads from `charts/theme.ts` as-is. No edits to that module.

### 3. Preferences page layout

Top to bottom:

1. **`PageHeader`** — title "Preferences", subtitle "Learned rules & corrections", right-aligned filters (rule type `Select`, enabled/disabled `Select`, `RefreshButton`).
2. **Learned Rules section** — section header with count, `DataTable` with columns: Rule, Type, Confidence, State (enabled/disabled toggle), row click opens `RuleDetailDrawer`.
3. **Corrections section** — section header with count + inline filters (field `Select`, task type `Select`), `DataTable` with pagination footer.

**No Appearance card.** The theme toggle lives only in the nav rail footer (Wave 2). No duplication.

**Empty state** (spec voice): "No rules learned yet. Donna picks these up as you correct her."

### 4. Backend change — bundled

The `?rule_id=<id>` addition to `GET /admin/preferences/corrections` ships in the same PR as the frontend migration. It is additive — old callers without `?rule_id=` still work. `RuleDetailDrawer` consumes this to fetch only the selected rule's corrections instead of fetching all 500 and filtering client-side (P1 perf fix).

### 5. Comparison drawer — full shape

Decided to build the full detail view: input/prompt, two-panel side-by-side output comparison, metadata row (models, latency, cost, quality Δ), win/loss/tie badge. Not a simplified "just two outputs" view.

---

## File changes

### New files

- `pages/Shadow/ComparisonDrawer.tsx` + `ComparisonDrawer.module.css`
- `pages/Shadow/Shadow.module.css`
- `pages/Preferences/Preferences.module.css`

### Modified files

- `pages/Shadow/index.tsx` — rebuild as one scrollable page, strip AntD
- `pages/Shadow/ShadowCharts.tsx` — become two inline `ChartCard`s
- `pages/Shadow/ComparisonTable.tsx` — migrate to `DataTable`, add row click → drawer
- `pages/Shadow/SpotCheckTable.tsx` — migrate to `DataTable`
- `pages/Preferences/index.tsx` — rebuild as one scrollable page, strip AntD
- `pages/Preferences/RulesTable.tsx` — migrate to `DataTable`
- `pages/Preferences/CorrectionsTable.tsx` — migrate to `DataTable`
- `pages/Preferences/RuleDetailDrawer.tsx` — migrate to primitive `Drawer`, use `?rule_id=`
- `src/api/preferences.ts` — add `rule_id` param to `fetchCorrections`
- `src/donna/api/routes/admin_preferences.py` — add `rule_id` query param to `list_corrections` handler (line 150)
- `tests/e2e/smoke/shadow.spec.ts` — expand coverage
- `tests/e2e/smoke/preferences.spec.ts` — expand coverage
- `tests/e2e/helpers.ts` — mock shapes for shadow + preferences endpoints

### Not touched

- `charts/theme.ts` (API boundary — wave 3 set it, wave 8 reads it)
- Nav rail / routing (no subroute split needed)

---

## Audit issues resolved

| ID | Severity | Issue | Resolution |
|---|---|---|---|
| — | P1 | `RuleDetailDrawer` fetches 500 rows for client-side filter | Backend `?rule_id=<id>` param, drawer fetches per-rule |
| — | P1 | Shadow table missing keyboard row navigation | `DataTable` primitive provides this |
| — | P2 | Shadow chart inline hex → tokens | Charts read from `charts/theme.ts` CSS tokens |
| — | P3 | Preferences page lacks empty state for zero rules | Empty state with Donna voice copy added |

---

## Testing

- Playwright smoke tests for both pages (happy-path per section).
- Shadow: page loads, charts render, comparisons table rows visible, row click → drawer opens with two output panels, spot checks section renders, keyboard nav works.
- Preferences: page loads, rules table renders, row click → drawer with per-rule corrections, corrections section renders with filters, empty state renders when zero rules.
- Backend: integration test — `GET /admin/preferences/corrections?rule_id=X` returns only that rule's corrections.
- End-of-wave verification: `grep -r "antd\|@ant-design" src/pages/Shadow/ src/pages/Preferences/` returns zero matches.
- Final step: run `impeccable:audit` skill and fix any issues found.
