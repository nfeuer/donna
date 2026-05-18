# Documentation System — Design Spec

> **Goal:** A global Claude Code skill + agent + reference standard that bootstraps, updates, and audits hosted documentation across all projects, using Donna's docs as the gold standard.

## Overview

Three files in `~/.claude/`:

| File | Role |
|------|------|
| `skills/update-docs/docs-standard.md` | The standard itself — taxonomy, writing conventions, quality bar, templates |
| `skills/update-docs/SKILL.md` | Interactive skill with three subcommands: `init`, default (update), `audit` |
| `agents/docs-updater.md` | Subagent for automated dispatch from other workflows |

The **docs-standard.md** is the centerpiece. Both the skill and agent reference it. It is also readable on its own as a standalone documentation standard.

## Taxonomy

The canonical section structure, adapted by project type. Sections are included or skipped based on what the project actually has.

| Section | Purpose | Include when |
|---------|---------|--------------|
| **Home** | What this is, how to use these docs, stack overview | Always |
| **Start Here** | Install, quickstart, conventions | Always |
| **Feature Map** | Capabilities overview with status and deep links | 3+ features |
| **Architecture** | Overview, data flow, component map | Multi-module projects |
| **Domain** | Per-subsystem deep dives (one page per major module) | Multi-module projects |
| **Workflows** | Step-by-step walkthroughs of concrete user flows | Always (at least 2) |
| **Config** | Auto-generated from config files (YAML/JSON/TOML) | Projects with config files |
| **Schemas** | Auto-generated from JSON schemas | Projects with schemas |
| **API Reference** | Auto-generated from docstrings (mkdocstrings/TypeDoc) | Always for Python/TS |
| **Development** | Contributing, testing, eval harness | Always |
| **Operations** | Docker, deployment, migrations, monitoring | Deployed services |
| **Troubleshooting** | Common issues and solutions, grows over time | Always |
| **Decisions** | Architecture Decision Records (key choices + reasoning) | Non-obvious design decisions exist |
| **Changelog** | What's new, summarized from commits/PRs | Always |
| **Glossary** | Domain terms with definitions and links to domain pages | 5+ domain-specific terms |
| **Canonical Specs** | Embedded spec documents | Projects with spec docs |

## Writing Conventions

### Voice and Structure
- Lead every page with a one-sentence summary of what this page covers
- No throat-clearing ("This page describes...") — start with the content
- Use tables for structured data, prose for narratives, Mermaid diagrams for flows
- Cross-reference related pages — no page should be an island
- Link to API Reference for code details, link to Workflows for "how to do X"
- Spec citations where applicable (e.g., `§3.2`)

### Quality Bar
- Every Domain page should be understandable without reading the code
- Every Workflow page should be followable as a step-by-step guide
- Auto-generated index pages should have useful context, not just file lists
- Internal links must resolve — no dead links
- Code examples must be current (not stale snippets from old versions)

### Page Templates

**Domain page:**
```markdown
# <Subsystem Name>

<One-sentence summary of what this subsystem does and why it exists.>

> Realizes: `spec_v3.md §X.Y`

## Overview
<2-3 paragraphs: what it does, how it fits into the system, key concepts.>

## Key Concepts
| Concept | Description |
|---------|-------------|

## Architecture
<How the subsystem is structured internally. Mermaid diagram if helpful.>

## Configuration
<What config drives this subsystem, with links to Config pages.>

## API
<Key public interfaces, with links to API Reference.>

## See Also
- [Workflow: <related workflow>](../workflows/<file>.md)
- [Config: <related config>](../config/<file>.md)
```

**Workflow page:**
```markdown
# Workflow: <Action Verb> a <Thing>

**Realizes:** `spec_v3.md §X.Y`

## Scenario
<Concrete example of when you'd do this.>

## Steps
1. **<Step name>.**
   <What happens, which module handles it, link to API Reference.>
2. ...

## What Can Go Wrong
| Symptom | Cause | Fix |
|---------|-------|-----|

## See Also
- [Domain: <related subsystem>](../domain/<file>.md)
```

**Decision (ADR) page:**
```markdown
# ADR-<NNN>: <Decision Title>

**Date:** YYYY-MM-DD
**Status:** accepted | superseded by ADR-<NNN> | deprecated

## Context
<What problem or question prompted this decision.>

## Decision
<What we decided and why.>

## Alternatives Considered
| Option | Pros | Cons |
|--------|------|------|

## Consequences
<What this means going forward — constraints, trade-offs, follow-up work.>
```

## Section Detection Logic

During `init`, the skill determines which taxonomy sections to include by probing the project:

| Section | Detection rule |
|---------|---------------|
| Home | Always |
| Start Here | Always |
| Feature Map | 3+ top-level source modules OR README lists features |
| Architecture | 3+ top-level source modules |
| Domain | 3+ top-level source modules (one page per module) |
| Workflows | Always (stub at least 2 from detected entry points) |
| Config | `config/` directory exists OR `*.yaml`/`*.toml` config files in root |
| Schemas | `schemas/` directory exists OR `*.schema.json` files found |
| API Reference | Always for Python/TypeScript projects |
| Development | Always |
| Operations | `Dockerfile*` or `docker-compose*` or `docker/` exists |
| Troubleshooting | Always (starts empty, grows over time) |
| Decisions | Deferred — created when first ADR is written |
| Changelog | Always (seeded from recent git log) |
| Glossary | Deferred — created when 5+ domain terms are identified |
| Canonical Specs | `spec*.md` or `docs/specs/` exists |

## Skill: `/update-docs`

### Subcommand: `init`

Bootstrap a documentation site from scratch.

**Workflow:**
1. Detect project type: Python (`pyproject.toml`), Node (`package.json`), Rust (`Cargo.toml`), Go (`go.mod`), or mixed
2. Detect existing docs framework (`mkdocs.yml`, `docusaurus.config.js`, `conf.py`, etc.)
3. If no framework exists:
   a. Scaffold `mkdocs.yml` from the standard Material config template (parameterized with project name, repo URL, source path, language)
   b. Create `docs/` with initial pages for all applicable taxonomy sections
   c. Create `scripts/gen_ref_pages.py` adapted to the project's source layout and language
   d. Create `docs/stylesheets/<project>.css` with base custom CSS
   e. Create `.github/workflows/docs.yml` for GitHub Pages deployment
4. If a framework already exists: map the standard taxonomy to its config format, add missing sections
5. Generate initial content by scanning:
   - `README.md` → Home page seed
   - Source modules → Domain page stubs (one per top-level module)
   - CLI entry points or API routes → Workflow stubs
   - Config files → Config section setup
   - Git log → Initial Changelog entries
6. Output a report: what was created, what needs hand-writing, what sections were skipped and why

### Subcommand: default (no args) — Diff-based update

Update docs after a feature is built.

**Workflow:**
1. Run `git diff main...HEAD --name-only` to identify changed files
2. Map changed files to affected doc pages:
   - Source file changed → corresponding Domain page, API Reference
   - Config file changed → Config page
   - New module added → flag "needs a new Domain page"
   - Route added/changed → flag "Workflows may need update"
   - Schema changed → note auto-update; check if Domain page needs prose update
3. For each affected doc page: read current doc + current code, update doc to reflect code
4. Check Changelog: does this branch warrant a new entry?
5. Check Glossary: scan diff for new domain-specific terms
6. Check Troubleshooting: scan for new error handling, edge cases worth documenting
7. Verify internal links still resolve
8. Output summary: what was updated, what was flagged for manual attention

### Subcommand: `audit` — Comprehensive drift scan

Full-codebase documentation health check.

**Workflow:**
1. Read every doc page and every source module
2. For each Domain page: verify it still matches the code
   - Are all public classes/functions mentioned?
   - Are described behaviors still accurate?
   - Are cross-references valid?
3. For each Workflow: verify steps match current code paths
4. Check for orphaned doc pages (docs for deleted modules)
5. Check for undocumented modules (source with no corresponding doc)
6. Check for stale Glossary entries, dead links, outdated code examples
7. Output a drift report:
   ```
   ## Documentation Audit Report

   ### Health Score: X/100

   ### Drift
   | Page | Issue | Severity |
   |------|-------|----------|

   ### Missing Documentation
   | Module | Recommended Section | Priority |
   |--------|-------------------|----------|

   ### Orphaned Pages
   | Page | Reason |
   |------|--------|

   ### Dead Links
   | Page | Link | Target |
   |------|------|--------|

   ### Recommendations
   1. ...
   ```

## Agent: `docs-updater`

A subagent for automated dispatch. Receives context about what changed and runs the diff-based update workflow.

**Dispatch contexts:**
- `pre-pr` skill: "check if docs need updating before this PR"
- `finishing-a-development-branch`: "update docs as part of wrap-up"
- Manual dispatch: "go update the docs for the changes on this branch"

**Behavior:**
1. Read `docs-standard.md` for conventions
2. Detect if a docs site exists; if not, recommend running `/update-docs init` first
3. Run the diff-based update workflow
4. Output a structured report: what was updated, what needs manual attention, whether docs are PR-ready

## MkDocs Material Config Template

Based on Donna's proven config. Parameterized fields: `site_name`, `site_description`, `site_url`, `repo_url`, `repo_name`, `docs_dir`, source paths, language-specific handler config.

Key features:
- Material theme with dark/light toggle
- Fonts: Inter (text), JetBrains Mono (code)
- Plugins: search, awesome-pages, section-index, gen-files, literate-nav, mkdocstrings
- Extensions: admonitions, code highlighting with line numbers, Mermaid diagrams, tabbed content, task lists, snippets
- Custom CSS for project branding
- GitHub Pages CI workflow

## Auto-Generation Script Template

A `gen_ref_pages.py` template adapted per project type during `init`:

- **Python**: Walk source tree, emit mkdocstrings pages (Donna pattern)
- **TypeScript/Node**: Walk source tree, emit stub pages linking to TypeDoc output or inline documentation
- **Config files**: Embed with syntax highlighting and source links
- **Schemas**: Embed JSON schemas with descriptions and source links

Generated once during `init`, committed to the repo, runs on every docs build via `gen-files` plugin.
