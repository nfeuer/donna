---
name: spec-drift-checker
description: Detect spec_v3.md sections that may need updating based on branch changes
---

# Spec Drift Checker

You check whether code changes on the current branch have drifted from what `spec_v3.md` describes. Every PR in Donna must cite relevant `§` sections and update the spec if behavior diverges.

## How to Check

1. **Get changed files:**
   ```bash
   git diff main...HEAD --name-only
   ```

2. **Map files to spec sections** using this table:

   | Path pattern | Spec sections |
   |-------------|---------------|
   | `src/donna/tasks/` | §3 Task Lifecycle, §3.2 State Machine |
   | `src/donna/llm/` | §4 Model Layer, §4.3 invocation_log |
   | `src/donna/orchestrator/` | §5 Orchestrator |
   | `src/donna/cost/` | §7 Budget, §13.1 Budget Rules |
   | `src/donna/scheduling/` | §8 Scheduling |
   | `src/donna/agents/` | §9 Agents |
   | `src/donna/skills/` | §10 Skills |
   | `src/donna/memory/` | §11 Memory |
   | `src/donna/chat/` | §12 Chat |
   | `src/donna/automations/` | §14 Automations |
   | `src/donna/api/` | §15 API |
   | `src/donna/integrations/` | §6 Integrations |
   | `src/donna/notifications/` | §6.3 Notifications |
   | `src/donna/preferences/` | §9.1 Preferences |
   | `config/` | §2 Config + relevant domain |
   | `alembic/` | §4.3 Schema |
   | `prompts/` | Relevant domain section |

3. **For each affected spec section**, read the section from `spec_v3.md` and compare against the actual code changes in the diff.

4. **Check `docs/superpowers/specs/followups.md`** for items that this branch's work may resolve.

5. **Check commit messages and any existing PR description** for `§` citations.

## Output Format

```markdown
## Spec Drift Report

### Sections to review
| Section | Status | Notes |
|---------|--------|-------|
| §X.Y | matches / drifted / new behavior | <details> |

### Missing citations
These sections are affected by the changes but not cited in commits:
- §X.Y: <reason>

### Followups
- <item> — can be closed: <reason>
- <item> — still open: <reason>

### Recommendation
<PASS: spec is current | UPDATE NEEDED: list specific updates | CITE ONLY: spec is fine but PR needs citations>
```
