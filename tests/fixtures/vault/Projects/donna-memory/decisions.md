# Decisions

- 2026-04-10: picked sqlite-vec over qdrant. One DB file, no daemon.
- 2026-04-12: picked MiniLM-L6-v2 over bge-small. 256 tokens fine for
  v1; revisit after dogfooding.
- 2026-04-18: chunker uses markdown headings, not sliding windows.
  Heading provenance is more useful than overlap granularity.
- 2026-04-20: sensitivity flag via `donna: local-only` frontmatter,
  not a separate config file.
