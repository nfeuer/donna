# Slice 12: Vault Plumbing

> **Goal:** Stand up an Obsidian-compatible vault on Donna's homelab as a Donna-owned workspace, with a safe write path (path validation, mtime-based conflict detection, git auto-commit, one-click undo) and the minimum tool surface for agents to read and write markdown. No embeddings, no semantic search, no episodic memory — those land in Slice 13.

## Relevant Docs

- `CLAUDE.md` (always)
- `spec_v3.md §1.3, §3.2.4, §7.3, §17` — design principles, integration pattern, agent safety, sandbox
- `docs/reference-specs/memory-vault-spec.md` — full design (to be added with this slice)
- `docs/domain/integrations.md` — existing integration module pattern (Gmail is the template)
- `docs/domain/memory-vault.md` — narrative companion (to be added with this slice)

## What to Build

1. **Config and pydantic model** (`config/memory.yaml` + `src/donna/config.py`):
   - New `MemoryConfig` root with `vault` (root path, git author, sync method, templates_dir) and `safety` (`max_note_bytes`, `path_allowlist`, `sensitive_frontmatter_key`) blocks. Only the vault + safety subsets are consumed in this slice; leave embedding / retrieval / sources blocks parseable but unused until Slice 13.
   - `load_memory_config(config_dir) -> MemoryConfig`.

2. **VaultClient** (`src/donna/integrations/vault.py`):
   - Mirror the Gmail client shape (single file, async, config-driven).
   - `read(path) -> VaultNote` (path, content, frontmatter dict, mtime, size).
   - `list(folder="", recursive=True) -> list[str]`.
   - `stat(path) -> (mtime, size)`.
   - FTS5 `search(query, limit)` over vault body via a SQLite virtual table scoped to the memory DB. Acceptable to defer FTS5 to Slice 13 if the index bloats this slice — flag in PR.
   - Parses YAML frontmatter via `python-frontmatter` (new dep).

3. **GitRepo wrapper** (`src/donna/integrations/git_repo.py`):
   - Subprocess-based, no `GitPython` dependency.
   - `init_if_missing()`, `commit(paths, message, author) -> sha`, `revert(n) -> list[sha]`, `log(limit)`.
   - Configures `user.name` / `user.email` on init; never touches global git config.

4. **VaultWriter** (`src/donna/integrations/vault.py`, same module):
   - Sole write path. Enforces: path containment under vault root; `.md` extension; `max_note_bytes`; optional `expected_mtime` for optimistic concurrency (raises `VaultWriteError(reason="conflict")` on mismatch); frontmatter preservation on update; auto git commit with structured message.
   - Methods: `write`, `delete`, `move`, `undo_last`.

5. **Tools** (`src/donna/skills/tools/`):
   - `vault_read`, `vault_write`, `vault_list`, `vault_link`, `vault_undo_last`. `vault_search` optional (see FTS5 note above).
   - Register in `skills/tools/__init__.py:register_default_tools()` via `partial()`, gated on `vault_client` / `vault_writer` kwargs (match the Gmail gating pattern).

6. **Agent allowlist** (`config/agents.yaml`):
   - Add the six/seven new tool names to `allowed_tools` for `pm`, `research`, `challenger`, `scheduler`. No new agent.

7. **Wiring** (`src/donna/cli_wiring.py`):
   - `_try_build_vault_client()` — non-fatal: return `None` if `memory.yaml` missing or vault root unreachable. Follow the Gmail non-fatal pattern line-for-line.
   - Pass `vault_client` / `vault_writer` into `register_default_tools`.

8. **Sync channel (WebDAV)**:
   - Add the Caddy WebDAV block to `docker/` (bind host/port from config). Cloudflare tunnel / basic-auth credentials loaded from env, not YAML.
   - Document in `docs/operations/` how clients connect (Obsidian desktop + mobile WebDAV).

9. **`scripts/dev_tool_call.py`** (if not present):
   - Thin CLI that loads `DEFAULT_TOOL_REGISTRY` and dispatches a tool by name. Used in verification and manual testing for this slice and Slice 13+.

10. **Write tests:**
   - Unit: path containment rejects `..`, absolute paths, symlink escape.
   - Unit: mtime conflict → `VaultWriteError(reason="conflict")`.
   - Unit: size cap enforcement.
   - Unit: frontmatter preserved on overwrite when new content omits it.
   - Unit: `GitRepo` init + commit + revert using `tmp_path` and real `git` subprocess.
   - Integration: end-to-end `vault_write` → file on disk, commit in git log, `vault_read` returns matching mtime; `vault_undo_last` removes it.

## Acceptance Criteria

- [ ] `config/memory.yaml` loads into `MemoryConfig` via `load_memory_config(config_dir)`; missing file is non-fatal at startup
- [ ] `VaultClient.read(path)` returns `VaultNote` with parsed YAML frontmatter
- [ ] `VaultClient.list()` returns forward-slash relative paths, respects `ignore_globs`
- [ ] `VaultWriter.write()` rejects paths with `..`, absolute paths, symlinks escaping root, non-`.md` extensions, or payload > `max_note_bytes`
- [ ] `VaultWriter.write()` with stale `expected_mtime` raises `VaultWriteError(reason="conflict")`
- [ ] Every successful write commits to the vault git repo with author `Donna <donna@homelab.local>` and a structured message
- [ ] `vault_undo_last` runs `git revert` on the last N commits and returns the revert SHAs
- [ ] Tools `vault_read`, `vault_write`, `vault_list`, `vault_link`, `vault_undo_last` registered in the default tool registry when `vault_client` / `vault_writer` are present
- [ ] `config/agents.yaml` updated so `pm`, `research`, `challenger`, `scheduler` can call the new tools
- [ ] WebDAV Caddy block runs behind the existing Cloudflare path; Obsidian desktop client can open the vault read/write
- [ ] Frontmatter preserved when an overwrite omits it (not silently stripped)
- [ ] `scripts/dev_tool_call.py vault_write --path Inbox/$(date +%F)-slice12.md --content '# Hi'` creates the file, commits it, and returns the commit SHA
- [ ] `pytest tests/unit/integrations/vault tests/integration/test_vault_writer.py` passes

## Not in Scope

- **No embeddings, no `MemoryStore`, no sqlite-vec.** Slice 13 adds those.
- **No chat / task / correction ingestion hooks.** Slice 14 adds those.
- **No Jinja templates under `prompts/vault/`.** Slice 15.
- **No `memory_search` tool.** Slice 13.
- **No Supabase sync for new tables** (there are none yet).
- **No Grafana dashboard for memory metrics** — Slice 13 onward.
- **No MiniLM model load** — delay until the embedding layer lands.
- **Attachments** (images, PDFs). V1 is `.md` only.

## Session Context

Load only: `CLAUDE.md`, this slice brief, `spec_v3.md §1.3 / §3.2.4 / §7.3 / §17`, `docs/domain/integrations.md`, `docs/domain/memory-vault.md` (new), and the parent plan at `/root/.claude/plans/what-are-some-additional-transient-truffle.md`.

## Handoff to Slice 13

Slice 13 consumes: a stable `VaultClient` / `VaultWriter` API; `MemoryConfig` with the `embedding`, `retrieval`, and `sources` blocks already parseable; and the `Inbox/`, `Meetings/`, `People/`, `Projects/`, `Daily/`, `Reviews/` directories pre-created per `safety.path_allowlist`. Confirm the chunk-size / embedding-model decision (parent plan §14 #2) before Slice 13 starts.
