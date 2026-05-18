---
name: spec-check
description: Audit current branch for spec_v3.md drift and missing section citations
---

# Spec Check

Audit the current branch for spec drift. Every PR must cite `spec_v3.md §` sections and update drifted sections.

## Workflow

1. **Identify what changed on this branch:**
   ```bash
   git diff main...HEAD --name-only
   ```

2. **Map changed modules to spec sections.** Key mappings:

   | Source module | Spec sections |
   |--------------|---------------|
   | `src/donna/tasks/` | §3 (Task Lifecycle), §3.2 (State Machine) |
   | `src/donna/llm/` | §4 (Model Layer), §4.3 (invocation_log) |
   | `src/donna/orchestrator/` | §5 (Orchestrator), §5.1 (Pipeline) |
   | `src/donna/integrations/` | §6 (Integrations) |
   | `src/donna/cost/` | §7 (Budget), §13.1 (Budget Rules) |
   | `src/donna/scheduling/` | §8 (Scheduling) |
   | `src/donna/agents/` | §9 (Agents) |
   | `src/donna/skills/` | §10 (Skills) |
   | `src/donna/memory/` | §11 (Memory) |
   | `src/donna/chat/` | §12 (Chat) |
   | `src/donna/automations/` | §14 (Automations) |
   | `src/donna/api/` | §15 (API) |
   | `src/donna/notifications/` | §6.3 (Notifications) |
   | `src/donna/preferences/` | §9.1 (Preferences) |
   | `config/` | §2 (Config), relevant domain section |
   | `alembic/` | §4.3 (Schema) |

3. **Grep spec_v3.md for each affected section** and check if the current code matches what the spec describes:
   ```bash
   grep -n "§<section>" spec_v3.md | head -5
   ```

4. **Check `docs/superpowers/specs/followups.md`** for any items related to this branch's work that can be closed.

5. **Output a report:**
   ```
   ## Spec Check Report

   ### Changed modules
   - <module>: §X.Y — <status: matches | needs update | new, not in spec>

   ### Recommended spec updates
   - §X.Y: <what to update and why>

   ### Followups to close
   - <followup item> — can be closed because <reason>

   ### PR description § citations
   Suggested citations for PR description:
   - §X.Y (<topic>)
   ```

## When to use
- Before creating a PR
- After finishing a slice
- When changing behavior, schema, routing, config contracts, or external integrations
