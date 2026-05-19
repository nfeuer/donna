# Skill System

The skill system is Donna's capability layer: YAML-defined skills with LLM execution, tool dispatch, and output validation. Each skill is the atomic unit of Donna's capability system — a repeatable, version-controlled recipe that can replace a raw Claude API call with a structured, cheaper, auditable alternative.

Skills are matched to incoming requests via embedding-based capability matching, executed through a multi-step pipeline, and validated against fixtures. The system self-improves through shadow sampling, automated promotion/demotion, and an evolution loop that rewrites underperforming skills.

---

## Architecture Overview

The skill system was built across five phases, each introducing a distinct layer of functionality.

### Phase 1-2: Foundation

Capability registry, multi-step skill executor, challenger refactor, tool dispatch, triage, run persistence, and dashboard routes. Ships disabled by default — no user-visible behavior change until explicitly activated.

### Phase 3: Lifecycle

Shadow sampling of trusted skills to detect drift, automated promotion/demotion gates (sandbox → shadow_primary → trusted), auto-drafting of new skills from high-frequency claude_native patterns, degradation detection via Wilson-score confidence intervals, and a nightly cron that orchestrates all lifecycle jobs.

### Phase 4: Evolution

Correction clustering for fast-path flagging, the evolution loop (Evolver, EvolutionInputBuilder, EvolutionGates, EvolutionScheduler) that rewrites degraded skills, and production wiring via `assemble_skill_system()` — a single lifespan helper that constructs and connects all components.

### Phase 5: Automations

Schedule-driven recurring skills (`automation` + `automation_run` tables) with cadence policy, alert conditions DSL, cost caps per run, and an async poll-loop scheduler. Distinct from user to-do tasks.

---

## Current Status

The system ships disabled by default. Setting `skill_routing_enabled=True` (via `AgentDispatcher`) activates skill routing. All runtime knobs live in `config/skills.yaml`. All five phases are shipped.

See the [open backlog](../../superpowers/followups/open-backlog.md) for known gaps (notably G-2: full config wiring).

**Spec Reference:** Realizes `spec_v3.md §23`.

---

## Reading Guide

| Topic | Page |
|-------|------|
| First-time setup, activation, troubleshooting | [Setup & Activation](setup.md) |
| Shadow sampling, lifecycle transitions, auto-drafting, nightly cron | [Lifecycle & Shadow](lifecycle.md) |
| Evolution loop, correction clusters, automations | [Evolution & Automations](evolution.md) |
| Module index, manual escalation, tool gap surfacing | [Reference](reference.md) |

---

## Related

- [Run a Skill](../../workflows/run-a-skill.md)
- [Add a New Skill](../../workflows/add-a-new-skill.md)
- [Domain: Agents](../agents.md)
- [Domain: Cost](../cost.md)
