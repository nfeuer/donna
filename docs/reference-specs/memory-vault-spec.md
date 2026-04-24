# Memory Vault — Reference Spec

> Design spec for the Obsidian vault plumbing. Companion to `docs/domain/memory-vault.md` (narrative) and `slices/slice_12_vault_plumbing.md` (slice brief). Authoritative for config schema, write protocol, and error taxonomy.

## 1. Configuration schema (`config/memory.yaml`)

```yaml
vault:
  root: /donna/vault                       # absolute path (container-local)
  git_author_name: Donna
  git_author_email: donna@homelab.local
  sync_method: webdav                      # webdav | syncthing | manual
  templates_dir: prompts/vault             # unused until slice 15
  ignore_globs:
    - ".obsidian/**"
    - ".trash/**"
    - ".git/**"

safety:
  max_note_bytes: 200000                   # hard cap per write payload
  path_allowlist:                          # top-level folders accepted for writes
    - Inbox
    - Meetings
    - People
    - Projects
    - Daily
    - Reviews
  sensitive_frontmatter_key: donna_sensitive

# unused until slice 13
embedding:
  model: sentence-transformers/all-MiniLM-L6-v2
  chunk_tokens: 512
  chunk_overlap: 64

# unused until slice 13
retrieval:
  top_k: 8
  min_score: 0.25

# unused until slice 14
sources:
  chat: false
  tasks: false
  corrections: false
```

`MemoryConfig` (`donna.config`) round-trips every block. Slice 12 consumes only `vault` + `safety`; the remaining blocks must stay parseable so slice 13+ can drop in without a schema break.

## 2. Read protocol (`VaultClient`)

| Method | Returns | Notes |
|---|---|---|
| `read(path)` | `VaultNote(path, content, frontmatter, mtime, size)` | Body is the post-frontmatter content. Raises `VaultReadError` on missing / path escape. |
| `list(folder="", recursive=True)` | `list[str]` | Forward-slash relative paths, filtered by `ignore_globs`. |
| `stat(path)` | `(mtime, size)` | |
| `extract_links(path)` | `list[str]` | Bare `[[target]]` names; aliases and sub-headings are stripped. |

All methods run blocking file I/O via `asyncio.to_thread`. Reads accept any `.md` file under the vault root (even outside `path_allowlist`) so agents can inspect `README.md`, templates, etc.

## 3. Write protocol (`VaultWriter`)

```text
write(path, content, expected_mtime=None, message=None) -> commit_sha
delete(path, message=None)                                 -> commit_sha
move(src, dst, message=None)                               -> commit_sha
undo_last(n=1)                                             -> list[revert_sha]
```

Every mutation follows this fixed order:

1. Size check on the payload (rejects before reading disk).
2. `_resolve_safe_path` — rejects `..`, absolute, non-`.md`, symlink escape, or folder outside `path_allowlist`.
3. If the target exists:
   - Compare on-disk mtime to `expected_mtime` (if supplied). Mismatch → `VaultWriteError(reason="conflict")`.
   - Refuse the write if existing frontmatter has the sensitive key set truthy (`reason="sensitive"`).
4. Parse incoming `content` via `python-frontmatter`. Merge with existing metadata: existing keys win only when the new content omits them (`_merge_frontmatter`).
5. Serialise and write.
6. `GitRepo.commit([relpath], message)` with a pinned author — returns the new SHA.
7. Log a `vault_write` / `vault_delete` / `vault_move` event.

## 4. Error taxonomy

`VaultWriteError(reason=…)` codes:

| Reason | Raised when |
|---|---|
| `path_escape` | Path resolves outside vault root (absolute, `..`, symlink escape). |
| `not_markdown` | Extension is not `.md`. |
| `outside_allowlist` | Top-level folder is not in `safety.path_allowlist`. |
| `too_large` | Payload exceeds `safety.max_note_bytes`. |
| `conflict` | `expected_mtime` stale, or destination of a `move` already exists. |
| `sensitive` | Existing frontmatter has `safety.sensitive_frontmatter_key` set truthy. |
| `missing` | `delete` / `move` source does not exist. |

## 5. Git layout

- One repo, rooted at `vault.root`. Created on first boot via `GitRepo.init_if_missing()`.
- Local `user.name` / `user.email` set on init; never `--global`.
- Every commit authored via `-c user.name=… -c user.email=…` so the repo config can drift without changing author metadata.
- Commit message format: `donna(slice12): <verb> <path>` (overridable per call).
- `undo_last` uses `git revert --no-edit` over the last *n* commits (newest first).

## 6. FTS5 note

The slice brief allows deferring FTS5 search to slice 13. Slice 12 ships **without** a `vault_search` tool: slice 13 owns the memory DB and will land full-text + semantic search together to avoid throwaway index maintenance.

## 7. Non-goals for slice 12

- Embeddings / `sqlite-vec` / `MemoryStore` (slice 13)
- Episodic ingestion hooks (slice 14)
- Jinja templates (slice 15)
- Attachments (images, PDFs) — vault is `.md` only for v1
- Supabase sync for new tables — no new tables yet
- Grafana dashboards for memory metrics — slice 13 onward
