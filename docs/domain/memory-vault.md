# Memory Vault

> Slice 12 — `slices/slice_12_vault_plumbing.md`. Design constraints trace back to `spec_v3.md §1.3 / §3.2.4 / §7.3 / §17`.

## Why a vault

Task state lives in SQLite, conversation context dies with the session, and there's no file-based workspace agents can hand back to the user. An Obsidian-compatible markdown vault gives Donna a durable, human-editable, version-controlled surface for meeting notes, people profiles, daily logs, and research artefacts. Slice 12 establishes the plumbing; slices 13–15 layer semantic retrieval, episodic ingestion, and template-driven writes on top.

## Architecture at a glance

| Piece | File | Responsibility |
|---|---|---|
| Config | `config/memory.yaml` + `donna.config.MemoryConfig` | Vault root, git author, safety envelope, ignore globs. |
| Read client | `donna.integrations.vault.VaultClient` | `read`, `list`, `stat`, `extract_links`. Async, read-only. |
| Write client | `donna.integrations.vault.VaultWriter` | `write`, `delete`, `move`, `undo_last`. Sole mutation path. |
| Git wrapper | `donna.integrations.git_repo.GitRepo` | `subprocess`-based `init_if_missing`, `commit`, `revert`, `log`. |
| Tools | `donna.skills.tools.vault_{read,write,list,link,undo_last}` | LLM-facing skill tools. |
| WebDAV | `docker/donna-vault.yml` + `docker/caddy/vault.Caddyfile.example` | Sync channel for Obsidian desktop / mobile clients. |

The client and writer mirror the Gmail integration line-for-line: single module per integration, async methods over `asyncio.to_thread`, non-fatal startup via `_try_build_vault_client` / `_try_build_vault_writer` in `donna.cli_wiring`.

## Safety envelope

`VaultWriter` rejects any write that violates the invariants in `spec_v3.md §7.3`:

1. Path must resolve under the configured vault root (no `..`, no absolute, no symlink escape).
2. Extension must be `.md`.
3. Top-level folder must be in `safety.path_allowlist` (`Inbox`, `Meetings`, `People`, `Projects`, `Daily`, `Reviews` by default).
4. Payload size ≤ `safety.max_note_bytes` (200 KB default).
5. If `expected_mtime` is supplied and differs from on-disk, the write fails with `VaultWriteError(reason="conflict")` **before** any disk change.
6. If the target exists with frontmatter and the new content omits it, the existing frontmatter is preserved on keys the new content does not supply.
7. Every successful mutation produces exactly one git commit with author `Donna <donna@homelab.local>` (from config) and a structured message.
8. `undo_last` always uses `git revert` — never `git reset` — so the audit trail is preserved.

Failures raise `VaultWriteError(reason=...)` with reason codes: `path_escape`, `not_markdown`, `outside_allowlist`, `too_large`, `conflict`, `sensitive`, `missing`.

## Agent surface

Agents declared in `config/agents.yaml` gain the vault tools once the writer is built at boot:

| Agent | Tools granted |
|---|---|
| `pm`, `scheduler`, `research`, `challenger` | `vault_read`, `vault_write`, `vault_list`, `vault_link`, `vault_undo_last` |

If `config/memory.yaml` is missing or the vault root is unreachable, the tools simply aren't registered — boot still succeeds, and the rest of the skill system keeps running.

## Sync channel

A Caddy container (`donna-vault` compose service) exposes the vault root over WebDAV with HTTP basic auth. Obsidian desktop (Remote Sync plugin), Obsidian mobile (WebDAV plugin), and any WebDAV-aware editor can mount the endpoint. Writes made by humans over WebDAV and writes made by agents via `VaultWriter` share the same on-disk repo, so git history reflects both.

See `docs/operations/vault-sync.md` for bring-up steps and client configuration.

## What this slice does **not** do

- No embeddings, vector store, or semantic search — slice 13 (`MemoryStore`, `sqlite-vec`, `VaultSource`).
- No chat / task / correction ingestion — slice 14.
- No Jinja templates under `prompts/vault/` — slice 15.
- No off-server backup push — the vault is on local NVMe, captured by the existing backup rotation (`docs/operations/backup-recovery.md`).

## Handoff contract for slice 13

Slice 13 inherits:

- A stable `VaultClient` / `VaultWriter` API (no breaking changes planned).
- `MemoryConfig` with the `embedding`, `retrieval`, and `sources` blocks already parseable (unused here).
- The six allowlisted directories pre-created on first boot.

The chunk-size and embedding-model decision is left for the slice-13 plan.
