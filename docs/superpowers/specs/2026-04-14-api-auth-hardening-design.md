# API Auth Hardening Design

**Date:** 2026-04-14
**Status:** Draft — awaiting review
**Audit reference:** Security audit identified 6 findings in `src/donna/api/`: unauthenticated `/admin/*` routers, unauthenticated config/prompt writes, fail-open LLM gateway key check, chat-API user impersonation + missing session ownership, Firebase JWT silent fallback to default user, CORS wildcard with credentials.

## Problem

Donna's FastAPI layer has no working authentication. The Firebase JWT code exists but is aspirational, fails open, and silently grants `user_id="nick"` to any valid Firebase token. `/admin/*` routers are mounted with a literal `# no auth required` comment. `PUT /admin/configs/*` and `PUT /admin/prompts/*` can be called by anyone and hot-reload into the running process — an attacker can swap `llm_gateway.yaml` to clear the API key and open an unlimited Claude relay against the $100/month cap. The chat API trusts `body["user_id"]` verbatim and does not scope session reads to a caller.

At the same time, Donna will soon be deployed to a remote Linux host behind Cloudflare Tunnel + Caddy, sharing the `houseoffeuer.com` domain with `immich`, `curator`, and `server-manager` — each of which already runs a two-layer auth pattern (IP gate + Immich-based identity). That pattern needs to be brought to Donna, adapted for Donna's async stack, and extended in three ways: a hardened email-verification path, long-lived mobile device tokens, and per-caller service keys for internal homelab callers.

## Goals

1. Close every finding from the 2026-04-14 audit.
2. Reuse the security properties of `immich-manager/shared/auth` — IP allowlist, magic-link verification, trust durations, access levels, and Immich as the identity provider — without importing sync code into Donna's async codebase.
3. Support a mobile app that moves across networks without prompting the user for email verification on every new IP.
4. Keep internal homelab services (LLM gateway callers) authenticated and attributed without human involvement.
5. Never fail open. Any missing config, any unreachable dependency, any unassigned user → reject, not default.
6. Single source of truth for which routes require which checks — a new route without an auth annotation must be **denied by default**, not accidentally public.

## Non-goals

- Replacing Immich as the identity provider. Immich remains the source of truth for "who is a valid human user" and handles password storage, MFA, session expiry, and account recovery. Donna only validates Immich cookies and maps the Immich user to a local `donna_user_id`.
- Per-user audit logging of every action (separate concern — handled by the existing `invocation_log` table and structured logging).
- Protecting Donna against a compromised Immich instance. If Immich is compromised, an attacker can already issue themselves arbitrary sessions, and Donna will honor them. Mitigation is out of scope.
- Protecting against a compromised host machine. If the attacker has root on the Linux box, all bets are off.

## Architecture overview

```
                        Internet
                           │
                           ▼
                  Cloudflare Tunnel
                           │
                           ▼
                         Caddy
                           │
          donna.houseoffeuer.com (same origin)
           │                         │
           ▼                         ▼
      /              /api/*
           │                         │
           ▼                         ▼
     donna-ui:8400           donna-api:8200  ──┐
     (React SPA)             (FastAPI)         │
                                               │
                                 ┌─────────────┼──────────────┐
                                 │  homelab Docker network    │
                                 │                            │
                                 │   ┌───────────┐            │
                                 │   │ curator   │────┐       │
                                 │   └───────────┘    │       │
                                 │   ┌───────────┐    ├──▶ /llm/completions
                                 │   │ health    │────┤    (service-key auth,
                                 │   └───────────┘    │     internal CIDR only,
                                 │   ┌───────────┐    │     NOT exposed via Caddy)
                                 │   │ manager   │────┘       │
                                 │   └───────────┘            │
                                 └────────────────────────────┘
```

Four distinct authentication layers, composed as FastAPI dependencies:

1. **Device token** (optional first check) — long-lived token stored in mobile keystore; if valid, skip IP gate entirely.
2. **IP gate** — per-source-IP allowlist with email-magic-link verification.
3. **Immich identity** — forwards `immich_access_token` cookie to Immich `/api/users/me`; maps Immich user ID → Donna `user_id` + role.
4. **Service caller** — internal-network-only dependency for `/llm/*`: source CIDR allowlist + per-caller API key.

Dependencies compose at the router level using a **deny-by-default base router**. Any new route is automatically denied unless it opts into a specific auth class.

### Route classification (normative)

| Route prefix | Auth class | Rationale |
|---|---|---|
| `GET /health` | `PUBLIC_LIVENESS` | Docker/Caddy healthcheck. Returns `{"status": "ok"}` only; no data. |
| `POST /auth/request-access` | `PUBLIC_AUTH_FLOW` | New IP must be able to reach it. Rate-limited, allowlist-checked. |
| `GET /auth/verify` | `PUBLIC_AUTH_FLOW` | Magic-link callback. Token is 256-bit opaque. |
| `POST /auth/verify` | `PUBLIC_AUTH_FLOW` | Programmatic verify from mobile app deep link. |
| `GET /auth/status` | `PUBLIC_AUTH_FLOW` | Used by the SPA to poll verification state. Returns only `{trusted: bool}`. |
| `POST /sms/inbound` | `PUBLIC_WEBHOOK_TWILIO` | Twilio posts from shared IPs; `X-Twilio-Signature` HMAC gate. |
| `/tasks/*`, `/schedule/*`, `/chat/*`, `/agents/*` | `USER` | `device_token_or(ip_gate + immich_user)`, scoped to `user_id`. |
| `/admin/*` | `ADMIN` | `device_token_or(ip_gate)` **plus fresh Immich login** (admin ops never accept device token alone) **plus** Donna `role=admin`. |
| `/llm/*` | `SERVICE` | Internal CIDR + per-caller API key. Never proxied through Caddy. |

The four auth classes are implemented as four FastAPI `APIRouter` factories. A developer adding a new route picks a class; the router's default dependencies are applied automatically. There is no way to mount a route without an auth class — the base `APIRouter` is not exported.

## Components

### 1. `src/donna/api/auth/` package (new)

Replaces the existing `src/donna/api/auth.py` (Firebase, deleted).

```
src/donna/api/auth/
├── __init__.py            # Public exports: dependency factories only
├── dependencies.py        # FastAPI Depends(...) functions
├── ip_gate.py             # Ported from immich-manager/shared/auth/ip_gate.py, async
├── immich.py              # Immich token forwarding + user resolution
├── device_tokens.py       # Issue, validate, revoke long-lived device tokens
├── service_keys.py        # Per-caller API key validation for /llm/*
├── email_allowlist.py     # Sync job: Immich admin API → allowed_emails table
├── email_sender.py        # Magic-link email via Donna's Gmail integration
├── trusted_proxies.py     # X-Forwarded-For resolution, CIDR allowlist
└── router_factory.py      # The four deny-by-default APIRouter factories
```

**Responsibilities (one purpose per module):**

- `ip_gate.py` — CRUD for `trusted_ips`, `verification_tokens`, `ip_connections`. Pure async SQL, no HTTP concerns. Port of `shared/auth/ip_gate.py` rewritten on `aiosqlite`. Same table schema. Same `check_ip_access` return contract: `{"action": "allow"|"challenge"|"block", "reason": str, "ip_record": dict|None}`.
- `immich.py` — Single function `resolve_immich_user(cookie_or_bearer)`: calls Immich `/api/users/me`, returns `{immich_user_id, email, name, is_admin}` or raises `HTTPException(401)`. Caches responses for 60 seconds keyed on token hash. Uses Immich URL from `config/auth.yaml`.
- `device_tokens.py` — `issue(user_id, label, user_agent, ip) -> str`, `validate(token) -> {user_id, device_id}|None`, `revoke(device_id)`, `list_for_user(user_id)`. Stores argon2 hashes; raw token is returned only from `issue` and never re-retrievable. Sliding-window expiry: every successful validate extends `expires_at` by the configured window.
- `service_keys.py` — `validate(caller_key, source_ip) -> {caller_id, budget_usd}|None`. Same argon2 hashing. Rejects if source_ip is outside `DONNA_INTERNAL_CIDRS`. The `X-Forwarded-Host` header is checked and the request is rejected if set (defense against accidental Caddy proxying of `/llm/*`).
- `email_allowlist.py` — Exposes `is_allowed(email) -> bool` and starts a background task on app startup that refreshes `allowed_emails` from Immich admin API every 15 minutes. On sync failure, serves stale data for up to 24h, after which the sync task marks itself unhealthy (visible in `/health`) but does **not** change the email check's behavior — a stale allowlist still blocks new emails.
- `email_sender.py` — Uses Donna's existing Gmail integration to send the magic-link email. Template is a plain-text + HTML message with the verify URL and a human warning "If you did not request this, ignore this email and revoke the IP at donna.houseoffeuer.com." The verify URL is constructed from a fixed base (no user-controlled components).
- `trusted_proxies.py` — `client_ip(request) -> str`: if `request.client.host` is in `DONNA_TRUSTED_PROXIES`, read the **last** entry in `X-Forwarded-For` (Caddy appends, and we only trust the immediate next hop). Otherwise, use `request.client.host`. This is the only place in the codebase that reads XFF. **All downstream checks read `client_ip(request)`, never `request.client.host` directly.**
- `router_factory.py` — Exports `public_liveness_router()`, `public_auth_router()`, `public_webhook_twilio_router()`, `user_router()`, `admin_router()`, `service_router()`. Each returns a new `APIRouter` with its class-appropriate `dependencies=[...]` pre-applied. A type alias `CurrentUser = Annotated[str, Depends(get_current_user_id)]` is exported for route handlers that need the resolved `user_id`.

### 2. `src/donna/api/auth/dependencies.py` — the composition layer

```python
# Public type aliases (imported by route handlers)
CurrentUser = Annotated[str, Depends(_resolve_user_id)]
CurrentAdmin = Annotated[str, Depends(_resolve_admin_user_id)]
CurrentServiceCaller = Annotated[ServiceCaller, Depends(_resolve_service_caller)]
```

`_resolve_user_id` runs in this order:

```
1. Is "Authorization: Bearer <token>" present and valid in device_tokens?
   → Return device_tokens.user_id.
2. Else: run ip_gate.check_ip_access(client_ip).
   → If action != "allow", raise 403 with {"error": "ip_not_trusted", "step": "request_access"}.
3. Resolve Immich session from cookie or Authorization header.
   → If missing, raise 401 with {"error": "unauthenticated", "login_url": "<immich_login>"}.
4. Look up users table: SELECT donna_user_id FROM users WHERE immich_user_id = ?.
   → If not found, raise 403 {"error": "user_not_provisioned"}. No silent fallback.
5. Return donna_user_id.
```

`_resolve_admin_user_id`:

1. Runs `_resolve_user_id` **without** the device-token shortcut. Admin ops always require a live Immich session. Rationale: device tokens outlive Immich sessions; we want admin changes to require re-auth.
2. Checks `users.role = 'admin'` in the Donna `users` table. If not, 403.
3. Returns `donna_user_id`.

`_resolve_service_caller`:

1. Check `client_ip(request)` is inside `DONNA_INTERNAL_CIDRS`. If not, 403.
2. Check `X-Forwarded-Host` header is absent. If present, 403 (defense against Caddy misconfiguration). Legitimate service callers reach `donna-api:8200` directly over the homelab Docker network — this header should never be set on a `/llm/*` request.
3. Validate `X-Donna-Service-Key` against `llm_gateway_callers` table. If missing or invalid, 401.
4. Return `{caller_id, monthly_budget_usd}`.

### 3. Database schema (Alembic migration `add_auth_tables.py`)

```sql
CREATE TABLE trusted_ips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | trusted | revoked
    access_level TEXT,                        -- user | admin
    trust_duration TEXT,                      -- 24h | 7d | 30d | 90d
    trusted_at DATETIME,
    expires_at DATETIME,
    verified_by TEXT,
    label TEXT,
    last_seen DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    source TEXT DEFAULT 'web',
    revoked_at DATETIME,
    revoked_by TEXT,
    revoke_reason TEXT
);
CREATE INDEX idx_trusted_ips_status ON trusted_ips(status);

CREATE TABLE verification_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,          -- sha256 of the random token
    ip_address TEXT NOT NULL,                 -- binds token to the requesting IP
    email TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    trust_duration TEXT DEFAULT '30d'
);
CREATE INDEX idx_verification_tokens_hash ON verification_tokens(token_hash);

CREATE TABLE ip_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    service TEXT,
    action TEXT,
    user_id TEXT
);
CREATE INDEX idx_ip_connections_ip ON ip_connections(ip_address);
CREATE INDEX idx_ip_connections_timestamp ON ip_connections(timestamp);

CREATE TABLE allowed_emails (
    email TEXT PRIMARY KEY,                   -- normalized lowercase
    immich_user_id TEXT NOT NULL,
    name TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    synced_at DATETIME NOT NULL
);

CREATE TABLE users (
    donna_user_id TEXT PRIMARY KEY,
    immich_user_id TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    name TEXT,
    role TEXT NOT NULL DEFAULT 'user',        -- user | admin
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME
);

CREATE TABLE device_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,          -- argon2 hash
    user_id TEXT NOT NULL,
    label TEXT,                                -- "Nick's iPhone 15"
    user_agent TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME,
    last_seen_ip TEXT,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME,
    revoked_by TEXT
);
CREATE INDEX idx_device_tokens_user ON device_tokens(user_id);
CREATE INDEX idx_device_tokens_expires ON device_tokens(expires_at);

CREATE TABLE llm_gateway_callers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_id TEXT NOT NULL UNIQUE,           -- e.g., "curator"
    key_hash TEXT NOT NULL,                   -- argon2 hash
    monthly_budget_usd REAL NOT NULL DEFAULT 0.0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    revoked_at DATETIME,
    revoke_reason TEXT
);
CREATE INDEX idx_llm_gateway_callers_enabled ON llm_gateway_callers(enabled);
```

All queries in `ip_gate.py`, `device_tokens.py`, and `service_keys.py` use `aiosqlite` with `?` placeholders. No f-strings, no `.format()`, no concatenation. A unit test asserts parameterized behavior by passing SQL-injection payloads as values and confirming they are treated as literals.

### 4. `config/auth.yaml` (new)

```yaml
ip_gate:
  default_trust_duration: 30d
  durations_allowed: [24h, 7d, 30d, 90d]
  rate_limit_per_ip:
    request_access: { max: 5, window_seconds: 3600 }
    verify:         { max: 10, window_seconds: 600 }

trusted_proxies:
  # CIDRs for Caddy's position. Donna will read X-Forwarded-For ONLY when
  # request.client.host is inside one of these.
  - 172.18.0.0/16   # homelab Docker network
  # Add the Cloudflared container's IP here once it's known.

internal_cidrs:
  # Source CIDRs allowed to call /llm/*.
  - 172.18.0.0/16

immich:
  internal_url: http://immich_server:2283
  external_url: https://immich.houseoffeuer.com
  admin_api_key_env: IMMICH_ADMIN_API_KEY
  user_cache_ttl_seconds: 60
  allowlist_sync_interval_seconds: 900
  allowlist_stale_tolerance_seconds: 86400

device_tokens:
  sliding_window_days: 90       # each successful use extends expires_at by this much
  absolute_max_days: 365        # hard cap from created_at; cannot be extended past this
  max_per_user: 10              # issuing an 11th revokes the oldest

email:
  from: "donna@houseoffeuer.com"
  subject: "Donna access verification"
  verify_base_url: "https://donna.houseoffeuer.com/auth/verify"
  token_expiry_minutes: 15

bootstrap:
  # First-run admin seed. The email here must already exist in Immich.
  # Once a row exists in the users table with role=admin, this is ignored.
  admin_email_env: DONNA_BOOTSTRAP_ADMIN_EMAIL
```

Config is loaded at startup, validated (type-checked and constraint-checked), and exposed on `app.state.auth_config`. Hot-reload of `auth.yaml` via the admin endpoint is **disabled** — changing auth config requires a process restart. This removes auth from the hot-reload attack surface entirely.

### 5. Caddy configuration

```caddy
donna.houseoffeuer.com {
    encode gzip zstd

    @api path /api/*
    handle @api {
        uri strip_prefix /api
        reverse_proxy donna-api:8200 {
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
            header_up X-Forwarded-Host {host}
        }
    }

    handle {
        reverse_proxy donna-ui:8400
    }

    log {
        output file /var/log/caddy/donna-access.log
    }
}
```

`/llm/*` is intentionally not routed. Any external attempt returns Caddy's default 404. Internal callers reach `http://donna-api:8200/llm/completions` directly over the `homelab` Docker network.

## Data flow

### First-time web login (cold start)

1. User visits `https://donna.houseoffeuer.com`. Caddy serves the static React bundle.
2. React app's root layout fires `GET /api/auth/status`. Donna returns `{trusted: false}` because this IP is pending.
3. React app renders `<RequestAccessPage />`.
4. User enters email, submits. React app POSTs `{email}` to `/api/auth/request-access`.
5. Donna: validate email regex → lowercase → query `allowed_emails` → generate 32-byte token → sha256 it → insert `verification_tokens` row with IP binding + 15-min expiry → email the link via Gmail integration. Response is `202 Accepted` regardless of whether the email was in the allowlist.
6. User opens email, clicks link → `https://donna.houseoffeuer.com/auth/verify?token=<opaque>`.
7. React app's `/auth/verify` route extracts the token, POSTs to `/api/auth/verify`.
8. Donna: sha256 the token → look up in `verification_tokens` → check unused + not expired + matching IP → mark used → insert/update `trusted_ips` with `status=trusted, access_level=user, trust_duration=30d` → return `{trusted: true, next: "immich_login"}`.
9. React app redirects to Immich login (`external_url` from config) with `returnUrl=https://donna.houseoffeuer.com/`.
10. User signs in to Immich. Immich sets `immich_access_token` cookie on `.houseoffeuer.com`. Browser redirects back.
11. React app loads normally. Next API call carries the Immich cookie.
12. Donna: resolve cookie via Immich → look up `users` table by `immich_user_id`. If no row exists for this user, **auto-provision** a new row: `donna_user_id = immich_user_id`, `email`/`name` from Immich, and `role = 'admin'` **if and only if** `email == DONNA_BOOTSTRAP_ADMIN_EMAIL` **and** no admin row currently exists in the table; otherwise `role = 'user'`. Every subsequent login is a simple lookup (no env-var check). Return data.

### First-time mobile app login

Identical to web steps 1-11, but step 12 also includes:

13. On the first authenticated response, Donna includes `{"device_token": "<opaque>"}` in the body. The app stores this in Keystore/Keychain.
14. All subsequent requests send `Authorization: Bearer <device_token>`. Steps 2, 3, 5-11 are bypassed for the lifetime of the token.

### Mobile app on a new network (device token already exists)

1. App issues `GET /api/tasks` with `Authorization: Bearer <device_token>`.
2. Dependency `_resolve_user_id`:
   - Look up `device_tokens` by argon2-verified hash. Row exists, not revoked, not expired. Return `user_id`.
   - IP gate **skipped**.
   - Immich check **skipped**.
3. Donna updates `device_tokens.last_seen`, `last_seen_ip`, and extends `expires_at` by the sliding window.
4. Response returns normally.

### Internal service call (curator requesting a completion)

1. Curator POSTs `http://donna-api:8200/llm/completions` with `X-Donna-Service-Key: <key>` and body.
2. `_resolve_service_caller`:
   - `client_ip(request)` is in `172.18.0.0/16` → pass.
   - No `X-Forwarded-Host` → pass.
   - Argon2-verify the key against `llm_gateway_callers.key_hash` rows where `enabled=1`. Find `caller_id='curator'` → pass.
3. Donna processes the request, logs `caller_id`, `tokens`, `cost_usd` to `invocation_log`.

## Error handling

- **Email allowlist sync fails:** Donna continues serving from the stale cache up to 24h. Logs `WARN email_allowlist_sync_stale seconds_since_success=...`. `/health` returns `{status: ok, warnings: [...]}` after 1h of failure. After 24h, `/health` returns `degraded` but the app keeps running.
- **Immich unreachable on a live request:** Return `503 {"error": "identity_provider_unavailable"}`. Do not fall through to a default user. Do not cache failures as "unauthenticated."
- **Gmail unreachable when sending magic link:** Return `202` to the client (no enumeration leak) but log `ERROR magic_link_send_failed` at the server and bump a Prometheus counter. Token is **not** inserted into the DB, so the user can retry.
- **Device token expired:** Return 401 with `{"error": "device_token_expired", "step": "relogin"}`. App clears its stored token, restarts the full login flow.
- **Rate limit exceeded on `/auth/request-access`:** Return `429 Retry-After: <seconds>`. Do not leak whether the email was valid.
- **Missing config:** App refuses to start. Refuses to start if `auth.yaml` is absent, if `trusted_proxies` is empty, if `internal_cidrs` is empty, if `IMMICH_ADMIN_API_KEY` is unset, or if no bootstrap admin is configured **and** the `users` table is empty.
- **Expired verification token used:** Return `{"error": "token_expired", "step": "request_access"}`. The token row is left in place until cleaned up by a nightly job that deletes rows `WHERE used=1 OR expires_at < now() - INTERVAL 1 day`.
- **Unknown email submitted to `/auth/request-access`:** Return `202` with the same body as success. Log `INFO ip_gate_unknown_email_attempted email_sha256=<hash> source_ip=<ip>`. Never log raw email.

## Testing

Unit tests:
- `ip_gate.check_ip_access` returns `allow`/`challenge`/`block` for each status combination (matrix test).
- SQL injection payload passed as `email` to `/auth/request-access` is treated as a literal (DB unchanged).
- SQL injection payload passed as `token` to `/auth/verify` is rejected by hash validation before reaching SQL.
- Device token argon2 hash round-trips.
- `X-Forwarded-For` from a non-trusted proxy is **ignored**; `request.client.host` is used.
- `X-Forwarded-For` from a trusted proxy extracts the correct client IP.
- `service_keys.validate` rejects a valid key presented from outside `internal_cidrs`.
- `service_keys.validate` rejects a valid key when `X-Forwarded-Host` is present.
- `_resolve_admin_user_id` rejects a valid device token (admin ops never accept device tokens).
- `email_allowlist.is_allowed` returns False for emails not synced from Immich.
- `allowed_emails` sync failure falls back to stale cache for 24h.

Integration tests (use httpx AsyncClient against a live FastAPI app with an in-memory SQLite):
- Full web login flow: unknown IP → request-access → verify → Immich mock → authenticated request.
- Full mobile flow: email verify → device token issued → subsequent request without IP gate.
- Admin flow: user role cannot reach `/admin/*`; admin role can.
- Replay attack on magic link: first verify succeeds, second verify fails with `token_used`.
- Cross-IP replay: magic link for IP A, verify from IP B → fails with `ip_mismatch`.
- CORS: requests from `evil.com` origin are rejected (no CORS middleware in prod config).
- Deny-by-default: a new route mounted on the bare `APIRouter` fails a startup lint check; only `user_router()`/`admin_router()`/etc. are permitted.
- Fail-closed: removing `IMMICH_ADMIN_API_KEY` from env prevents app startup.

End-to-end threat model tests (documented as test cases even if manual):
- XFF spoofing from outside trusted proxies.
- Docker network pivot: a rogue container on `homelab` without a service key cannot reach `/llm/*`.
- Stale device token on a new IP still works.
- Revoking a device token takes effect within one request.

## Migration plan

Implementation order (each step can be merged independently and does not break the previous):

1. **Alembic migration** — add the 7 new tables. No code yet. `pytest` passes.
2. **Port `ip_gate.py` as pure async module** — unit tests against in-memory SQLite. No HTTP routes yet.
3. **Port `immich.py` + `email_allowlist.py`** — standalone modules with unit tests.
4. **Add `trusted_proxies.py`** — unit tests for XFF resolution.
5. **Add `router_factory.py` + `dependencies.py`** — the composition layer. At this point all pieces exist but no route uses them.
6. **Add `/auth/*` routes** — request-access, verify, status. Public auth class.
7. **Add `email_sender.py`** — wire Gmail integration to verify flow. Manual test locally.
8. **Migrate `/tasks`, `/schedule`, `/agents`** — flip each router to `user_router()`. Existing tests must pass.
9. **Migrate `/chat/*`** — same, and remove `body.get("user_id")` trust path. Add ownership checks on session lookups.
10. **Migrate `/admin/*`** — flip to `admin_router()`. Remove `# no auth required` comment. Add new `/admin/ips` and `/admin/devices` routes.
11. **Add `service_keys.py` + migrate `/llm/*`** — flip to `service_router()`. Delete the fail-open `_require_api_key` function. Seed `llm_gateway_callers` table from a new `config/service_callers.yaml` at startup (only creates rows that don't already exist — never overwrites).
12. **Delete Firebase code** — remove `src/donna/api/auth.py` and the `pyjwt`/`aiohttp` imports it used. Remove `FIREBASE_*` env vars from `.env.example` and Docker compose.
13. **Remove CORS middleware** — since Caddy makes UI and API same-origin, the `CORSMiddleware` is deleted entirely. Add a startup assertion that `DONNA_CORS_ORIGINS` is either unset or a concrete non-`*` list.
14. **Caddy config update** — deploy the new `donna.houseoffeuer.com` block.
15. **`donna-ui` changes** — add `<RequestAccessPage />`, global 401/403 handlers, `/admin/access` panel for IPs and devices.
16. **Admin bootstrap** — document in `SETUP.md`: set `DONNA_BOOTSTRAP_ADMIN_EMAIL`, deploy, request access, verify, log in to Immich, confirm row exists in `users` with `role=admin`, remove env var.

Each step is a separate commit. Steps 1-11 can be done without touching the UI; step 15 is the UI work. Step 12 removes dead code.

## Security properties summary

- **Fail closed on every branch.** Missing config → app refuses to start. Unknown user → 403, not default. Empty key → reject. Unreachable Immich → 503, not 200.
- **Deny by default on every new route.** The bare FastAPI `APIRouter` is not exported; developers can only mount routes on one of the four auth-class factories.
- **No single point of compromise.** A stolen device token grants user scope only, never admin. A compromised container on `homelab` still needs a per-caller key to reach `/llm/*`. A compromised Caddy config cannot reach `/llm/*` because the app-level middleware rejects any request with `X-Forwarded-Host` set.
- **No user enumeration.** `/auth/request-access` returns the same 202 for valid, invalid, malformed, and out-of-allowlist emails. Unknown-email attempts are logged with a sha256 of the email, never the raw address.
- **SQL injection resistant.** Every auth-table query uses `aiosqlite` parameterized placeholders. Regression test asserts this with adversarial payloads.
- **XFF-spoof resistant.** X-Forwarded-For is only trusted from `DONNA_TRUSTED_PROXIES`. Every other request uses `request.client.host`.
- **Magic-link hardened.** Tokens are 256-bit opaque, sha256-hashed in DB, bound to the requesting IP, single-use, 15-min expiry, and revokable.
- **Admin actions require fresh Immich login.** Device tokens alone cannot authorize admin routes.
- **Per-caller budget attribution.** Every `/llm/*` call logs `caller_id` + `cost_usd`. Per-caller monthly caps bound blast radius of a leaked key.

## Open questions

None — all questions raised during brainstorming were resolved before this spec was written. Any further questions surfaced during implementation should be resolved against this document or, if they require scope changes, kicked back to a follow-up brainstorming pass.
