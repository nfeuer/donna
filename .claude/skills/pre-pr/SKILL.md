---
name: pre-pr
description: Run full pre-PR checklist — tests, lint, typecheck, spec citations, migration heads, UI build
---

# Pre-PR Checklist

Run before creating a pull request. Catches the common mistakes: failing tests, lint violations, spec drift, migration conflicts, and missing UI builds.

## Checklist

Run these checks in order. Stop and fix issues as they arise.

### 1. Tests
```bash
python -m pytest tests/unit/ -m "not slow and not llm" -x -q --tb=short
```
If integration tests are relevant to the changes:
```bash
python -m pytest tests/integration/ -x -q --tb=short
```

### 2. Lint
```bash
ruff check src/ tests/
```
Auto-fix if needed: `ruff check --fix src/ tests/`

### 3. Type Check
```bash
mypy src/donna/
```

### 4. Alembic Migration Heads
```bash
alembic heads
```
Must show exactly ONE head. If multiple, merge them:
```bash
alembic merge heads -m "merge <description>"
```

### 5. UI Build (if frontend files changed)
Check if UI files changed:
```bash
git diff main...HEAD --name-only | grep "^donna-ui/" | head -5
```
If yes, verify the build:
```bash
cd donna-ui && npm run build && cd ..
```

### 6. UI TypeScript Check (if frontend files changed)
```bash
cd donna-ui && npx tsc --noEmit && cd ..
```

### 7. Documentation Update
Dispatch the `docs-updater` agent (from `~/.claude/agents/docs-updater.md`) to check if docs need updating based on this branch's changes. The agent reads the documentation standard, diffs the branch against main, updates affected pages, and reports what changed. Review its output before continuing.

### 8. Spec Check
Identify changed modules and their spec sections:
```bash
git diff main...HEAD --name-only | grep "^src/donna/"
```
Cross-reference with `spec_v3.md`. Check if any changed behavior needs a spec update. Use the `spec-check` skill for a detailed audit.

### 9. Followups Check
Review `docs/superpowers/specs/followups.md` for items related to this branch's work that should be closed or updated.

### 10. Uncommitted Changes
```bash
git status
git diff --stat
```
Make sure everything intended is committed.

## Output

Report results as:
```
## Pre-PR Checklist

- [x] Tests: XX passed
- [x] Lint: clean
- [x] Types: clean
- [x] Migration heads: 1 (abc123)
- [x] UI build: clean (or N/A)
- [x] UI types: clean (or N/A)
- [x] Docs: updated domain/chat.md, changelog.md (or: no docs affected)
- [ ] Spec: §X.Y may need update — <reason>
- [x] Followups: none to close (or: closed X)
- [x] Working tree: clean

Ready for PR: YES / NO — <blocking issues>
```
