---
name: new-slice
description: Scaffold a new development slice brief with acceptance criteria and spec references
disable-model-invocation: true
---

# New Slice

Scaffold a new slice brief in `slices/`. Slices are the unit of development work in Donna — each one is a self-contained deliverable with spec references and acceptance criteria.

## Workflow

1. **Determine the next slice number.** List `slices/` and find the highest `slice_NN` number. Increment by 1.

2. **Ask the user for:**
   - Short name (snake_case, used in filename)
   - One-sentence goal
   - Which `spec_v3.md §` sections are relevant
   - Any related upstream slices

3. **Create the file** at `slices/slice_<NN>_<name>.md` using the template below.

4. **Remind the user** to read the referenced spec sections before starting work.

## Template

```markdown
# Slice <NN>: <Title>

> **Goal:** <one-sentence goal>

## Spec Reference

**Canonical spec:** `spec_v3.md`
**Sections this slice realizes:** §X.Y, §X.Z
**Related upstream slices:** slice_<NN> (<name>)

This slice is bound to the canonical spec. Read the referenced sections before starting work. Cite the relevant `§` in the PR description.

## Acceptance Criteria

- [ ] <criterion 1>
- [ ] <criterion 2>
- [ ] <criterion 3>
- [ ] All new modules have Google-style docstrings
- [ ] pytest passes (unit + integration)
- [ ] ruff check + mypy clean
- [ ] Alembic migration (if schema changes)
- [ ] spec_v3.md updated if behavior diverges

## Out of Scope

- <explicitly excluded items>

## Notes

- <implementation hints, constraints, or context>
```

## Conventions
- Filename: `slice_<NN>_<snake_case_name>.md`
- Always include the spec reference section — every slice traces to `spec_v3.md`
- Acceptance criteria should be testable, not vague
- Include the standard quality checks (pytest, ruff, mypy, docstrings)
