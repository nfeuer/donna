---
date: 2026-04-21
attendees: [nick, claude]
type: design-review
---

# Design Review — MemoryStore

## Context

The MemoryStore wraps a sqlite-vec virtual table alongside the regular
SQLite task DB. Every document is chunked, embedded, and stored with
`heading_path` provenance.

## Decision

Use MiniLM-L6-v2 with a 256-token chunk cap and 32-token overlap.
Alternatives (bge-small, Voyage-3-lite) considered and deferred.

## Reference implementation

```python
def search(query: str, k: int = 8) -> list[RetrievedChunk]:
    vec = embed(query)
    return store.match(vec, k=k)
```

The code block above should round-trip through the chunker intact
because it fits inside a single chunk window.
