# Slices

Phase 1 was built as 12 ordered slices, each with acceptance criteria.
Phase 2 (memory + vault) continues the same pattern. Slices live at the
repo root under
[`slices/`](https://github.com/nfeuer/donna/tree/main/slices).

| Slice | Theme |
|---|---|
| 00 | Scaffold — repo skeleton, health endpoint, logging |
| 01 | Database — SQLite + Alembic baseline schema |
| 02 | Model parsing — `parse_task` skill + router |
| 03 | Discord — bot + slash commands |
| 04 | Calendar — Google Calendar sync |
| 05 | Reminders & digest — cadence, daily digest |
| 06 | Dedup & cost — fuzzy + LLM dedup, budget guard |
| 07 | SMS escalation — Twilio SMS, escalation ladder |
| 08 | Email corrections — Gmail ingest, correction log |
| 09 | Observability & backup — Grafana/Loki, backup job |
| 10 | Multi-user API — FastAPI + Firebase auth |
| 11 | Flutter UI — sibling repo |
| 12 | Vault plumbing — Obsidian-compatible git-backed markdown vault, WebDAV, `vault_*` tools |
| 13 | Memory store — `MemoryStore`, sqlite-vec, `VaultSource`, `memory_search` |
| 14 | Episodic sources — `ChatSource` / `TaskSource` / `CorrectionSource`, `donna memory backfill` CLI |
| 15 | Template writes + meeting notes — *brief only (in flight)* |

Each slice file contains narrative + acceptance bullets. The
`VERIFICATION_REPORT.md` at the repo root audits delivery.
