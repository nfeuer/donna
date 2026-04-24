---
project: donna-memory
status: in-progress
---

# Donna Memory

## Motivation

The user forgets to capture tasks and rarely checks the task list. A
semantic memory layer lets Donna pull back relevant prior notes when
drafting responses — reducing the effort to keep context current.

## Architecture

### Storage

Two ORM tables (`memory_documents`, `memory_chunks`) plus a
`vec_memory_chunks` virtual table powered by sqlite-vec. All three
live in the same `donna_tasks.db` file.

### Ingestion

- **Vault source** (slice 13): watches the Obsidian vault for
  changes and keeps the index in sync.
- **Chat source** (slice 14): writes salient chat turns through to
  the store.
- **Correction source** (slice 14): mines correction logs for
  canonical task-shape examples.

### Retrieval

One SQL join across the three tables, ordered by vec0 distance,
filtered by `retrieval.min_score`. The tool returns provenance-tagged
chunks so agents can cite the source path + heading.

## Risks

- Embedding model drift when we upgrade past MiniLM-L6-v2.
- Silent truncation for chunks that exceed the model window.
