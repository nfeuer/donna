# Donna Vault Fixtures

This directory mirrors an Obsidian vault that Donna owns. It exists so
integration tests (and `scripts/dev_tool_call.py`) have realistic
markdown to chunk + embed. Do not treat this as documentation — it is
test data.

Conventions:

- Folders under the repo's `safety.path_allowlist` (Inbox, Meetings,
  People, Projects, Daily, Reviews) are the ones Donna will write to.
- `Templates/` and `.obsidian/` are deliberately included so the
  ingest path can exercise `ignore_globs`.
- Notes with `donna: local-only` frontmatter are sensitive — the
  retrieval pipeline surfaces that flag on `RetrievedChunk.metadata`.
