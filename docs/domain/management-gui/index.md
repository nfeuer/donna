# Management GUI

Developer-facing control panel for monitoring, debugging, and configuring the Donna AI assistant system.

**Separate from the end-user Flutter app** — this is the development/ops tool for understanding agent behavior, tracking costs, iterating on prompts/configs, and debugging data flows.

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Frontend | React 18 + Vite + TypeScript | SPA with client-side routing |
| Components | Ant Design 5 (dark theme) | Full admin component library |
| Charts | Recharts | Time series, bar, area, pie charts |
| HTTP Client | Axios | Proxied through Vite in dev |
| Backend | FastAPI (existing app, extended) | New `/admin/*` routes on port 8200 |
| Deployment | Docker (nginx) + compose | Port 8400 in production |
| Dev server | Vite | Port 5173, proxies to FastAPI |

## Architecture

```
donna-ui (React SPA, port 5173/8400)
    │
    ├── /admin/* ──► FastAPI (port 8200)
    │                   ├── SQLite (donna_tasks.db)
    │                   ├── Loki HTTP API (port 3100)
    │                   └── Config/prompt files (disk)
    │
    └── Static assets (nginx in production)
```

The GUI communicates exclusively through the FastAPI admin API. No direct database or Loki access from the frontend.

## Design Decisions Made

1. **Separate admin API prefix** (`/admin/*`) rather than a separate FastAPI service — keeps deployment simple, shares DB connection, avoids service sprawl.
2. **No auth on admin routes** — this is a local dev tool. Can add admin auth later if exposed externally.
3. **Ant Design over shadcn/ui** — richer out-of-the-box admin components (tables, trees, drawers, forms).
4. **Loki as primary log source** with SQLite fallback — structured logs already flow to Loki via Promtail. Direct SQLite queries cover invocation_log when Loki is down.
5. **Manual refresh + 30s auto-refresh on dashboard** — avoids WebSocket complexity for a dev tool.
6. **pnpm** as package manager — fast, disk-efficient.
7. **Dark theme only** — this is a developer tool, dark mode is the default.

## Auth Note

**If `/admin/*` is ever exposed externally**, the minimum bar would be:

1. A shared-secret bearer token (or Cloudflare Access / Tailscale header) enforced via a single FastAPI dependency — `Depends(require_admin)` — applied at the router layer so every `admin_*` router inherits it in one place.
2. Write-side endpoints (`PUT /admin/configs/{filename}`, `POST /admin/skills/{id}/state`, automation create/update/delete, `POST /admin/skill-runs/{id}/capture-fixture`) rate-limited more aggressively than read endpoints, and logged to `invocation_log` with caller identity.
3. No new persistence — reuse the existing access/auth infrastructure in `src/donna/api/routes/admin_access.py` (IP / device / caller tables) rather than adding a parallel admin-user table.

This is a note, not a plan — implementation is deferred until the tool leaves the loopback.

## Reading Guide

| Topic | Page |
|-------|------|
| All `/admin/*` API endpoints | [API Endpoints](api.md) |
| Page descriptions and UX features | [Pages](pages.md) |
| File structure, backend routes, Docker, build history | [Reference](reference.md) |

## Related

- [Domain: Insights](../insights.md)
- [Domain: API & Auth](../api.md)
