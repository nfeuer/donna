# Session 1 — Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the one externally-reachable HIGH-severity vulnerability (six unauthenticated `/admin` route modules) and shrink the blast radius of the remaining medium findings, without touching Google Calendar OAuth (owner is handling the permanent fix).

**Architecture:** The API already has a deny-by-default auth factory (`donna.api.auth.router_factory`); six route modules regressed to bare `APIRouter()` and slipped past it. The fix is to route them back through `admin_router()` and add a whole-app invariant test so it can never regress. Then reduce exposure: bind sensitive host ports to loopback, bind automation ownership to the authenticated principal, and remove a weak default.

**Tech Stack:** FastAPI, pytest (`asyncio_mode=auto`), Docker Compose.

**Spec references:** `spec_v3.md` §27 (admin API), §28 (auth modules). CLAUDE.md principle #2 "Safety first" (skill autonomy + human gate are safety controls that must not be unauthenticated).

## Global Constraints

- Async everywhere; type hints on all signatures; structured logging via `structlog` (no `print`). *(CLAUDE.md)*
- Every degraded fallback calls `dispatch_fallback_alert()` or logs `event_type="fallback_activated"`. *(CLAUDE.md)*
- `mypy --strict` and `ruff` (E/F/I/N/W/UP/B/SIM/RUF) must pass. *(pyproject.toml)*
- Run `pytest` before and after changes. *(CLAUDE.md)*
- Import factory as the existing modules do: `from donna.api.auth import admin_router` then `router = admin_router()`. *(pattern: `api/routes/admin_tasks.py:16,19`)*

**Out of scope (deferred, see §Deferred):** OAuth token-store encryption/relocation (couples to owner's calendar-OAuth work); browser-sidecar network isolation; internal-CIDR service-key gating.

---

### Task 1: Guard the six unauthenticated `/admin` route modules + add a regression invariant

The vulnerability. `api/__init__.py:472-477` mounts these six under `/admin`, but each declares `router = APIRouter()` with no auth (verified: grep shows zero `Depends`/`CurrentAdmin` in all six). This exposes mutating endpoints unauthenticated: `POST /admin/skills/{id}/state` (promote to `trusted`), `POST /admin/skills/{id}/flags/requires_human_gate` (flip a safety flag), `POST /admin/automations` (create budget-spending automation), plus dismiss/draft/capture-fixture.

**Files:**
- Modify: `src/donna/api/routes/capabilities.py:11`
- Modify: `src/donna/api/routes/skills.py:21`
- Modify: `src/donna/api/routes/skill_drafts.py:10`
- Modify: `src/donna/api/routes/skill_candidates.py:11`
- Modify: `src/donna/api/routes/skill_runs.py:18`
- Modify: `src/donna/api/routes/automations.py:15`
- Test: `tests/unit/test_auth_router_factory.py` (extend)

**Interfaces:**
- Consumes: `donna.api.auth.admin_router` (re-exported from `donna.api.auth`), `donna.api.auth.router_factory._admin_dep` (the callable bound by `admin_router()`).
- Produces: every module's `router.dependencies` now contains `Depends(_admin_dep)`; the app-wide invariant test asserts no `/admin` route is unguarded.

- [ ] **Step 1: Write the failing invariant test**

Add to `tests/unit/test_auth_router_factory.py`:

```python
import pytest

from donna.api.auth.router_factory import _admin_dep

# The six modules that must be admin-guarded (regression guard for the
# 2026-07-02 audit finding: bare APIRouter() bypassed deny-by-default).
ADMIN_GUARDED_MODULES = [
    "capabilities",
    "skills",
    "skill_drafts",
    "skill_candidates",
    "skill_runs",
    "automations",
]


@pytest.mark.parametrize("mod_name", ADMIN_GUARDED_MODULES)
def test_admin_route_module_carries_admin_dep(mod_name):
    import importlib

    mod = importlib.import_module(f"donna.api.routes.{mod_name}")
    dep_callables = [d.dependency for d in mod.router.dependencies]
    assert _admin_dep in dep_callables, (
        f"donna.api.routes.{mod_name}.router is not admin-guarded"
    )


def _route_guarded_by(route, dep_fn) -> bool:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = list(dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is dep_fn:
            return True
        stack.extend(d.dependencies)
    return False


def test_no_admin_route_is_unguarded():
    """Whole-app invariant: every route under /admin requires admin auth.

    Catches future modules that regress to bare APIRouter().
    """
    from donna.api import create_app

    app = create_app()
    unguarded = [
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin")
        and not _route_guarded_by(route, _admin_dep)
    ]
    assert unguarded == [], f"Unguarded /admin routes: {unguarded}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /mnt/donna/donna && python -m pytest tests/unit/test_auth_router_factory.py -q`
Expected: FAIL — `test_admin_route_module_carries_admin_dep[capabilities]` (and the other five) fail with "not admin-guarded"; `test_no_admin_route_is_unguarded` fails listing the six modules' paths.

- [ ] **Step 3: Guard each of the six modules**

In each file, replace the bare import + construction. Pattern (shown for `automations.py`):

```python
# add to the imports near the top (with the other donna.api imports):
from donna.api.auth import admin_router

# replace:
#   router = APIRouter()
# with:
router = admin_router()
```

Do this in all six: `capabilities.py`, `skills.py`, `skill_drafts.py`, `skill_candidates.py`, `skill_runs.py`, `automations.py`. Leave the existing `from fastapi import APIRouter, ...` line intact only if `APIRouter` is still referenced elsewhere in the file; if not, drop `APIRouter` from that import to satisfy ruff F401. (Verify per file with `ruff check <file>`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /mnt/donna/donna && python -m pytest tests/unit/test_auth_router_factory.py -q`
Expected: PASS (all six parametrized cases + both invariants).

- [ ] **Step 5: Verify no route behavior regressed**

Run: `cd /mnt/donna/donna && python -m pytest tests/unit tests/integration -k "skill or automation or capabilit or admin or auth" -q`
Expected: PASS. (If any test called these endpoints without auth and now gets 401/403, update it to use the authenticated test client, mirroring `tests/integration/test_auth_end_to_end.py`.)

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/routes/{capabilities,skills,skill_drafts,skill_candidates,skill_runs,automations}.py tests/unit/test_auth_router_factory.py
git commit -m "fix(api): guard six /admin route modules with admin_router() + invariant test (§28)"
```

---

### Task 2: Bind automation ownership to the authenticated admin (close the IDOR)

`POST /admin/automations` reads `user_id` straight from the request body (`automations.py:24` field, `:189` `create(user_id=body.user_id, ...)`). Even behind admin auth (Task 1), this lets a caller create automations under an arbitrary `user_id`. Bind ownership to the authenticated principal instead.

**Files:**
- Modify: `src/donna/api/routes/automations.py` (create handler ~:158-210, request model ~:23-48)
- Test: `tests/integration/test_admin_automations.py` (create if absent) or extend existing automations test.

**Interfaces:**
- Consumes: `donna.api.auth.CurrentAdmin` (`Annotated[str, Depends(_admin_dep)]` — resolves to the authenticated admin's `donna_user_id`).
- Produces: created automation's `user_id` equals the authenticated principal regardless of body content.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_admin_automations.py
import pytest


@pytest.mark.asyncio
async def test_create_automation_ignores_body_user_id(admin_test_client):
    client, authed_user_id = admin_test_client  # fixture: authed as an admin
    resp = client.post(
        "/admin/automations",
        json={
            "user_id": "someone_else",   # attacker-supplied
            "name": "watch",
            "capability_name": "product_watch",
            "inputs": {},
            "trigger_type": "on_schedule",
            "schedule": "0 9 * * *",
            "created_via": "api",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["user_id"] == authed_user_id
```

If no `admin_test_client` fixture exists, model it on `auth_test_app` in `tests/integration/test_auth_end_to_end.py` (provision a `role='admin'` user, mount the automations router, present the Immich bearer). Add the fixture to `tests/integration/conftest.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd /mnt/donna/donna && python -m pytest tests/integration/test_admin_automations.py -q`
Expected: FAIL — returned `user_id` is `"someone_else"`.

- [ ] **Step 3: Use the authenticated principal in the handler**

In `src/donna/api/routes/automations.py`:

```python
from donna.api.auth import CurrentAdmin  # add with the other donna.api imports

@router.post("/automations", status_code=201)
async def create_automation(
    body: CreateAutomationRequest,
    request: Request,
    admin_user_id: CurrentAdmin,      # authenticated principal
) -> dict[str, Any]:
    ...
    row = await repo.create(
        user_id=admin_user_id,        # was body.user_id
        name=body.name,
        ...
    )
```

Then remove `user_id` from `CreateAutomationRequest` (it is now server-derived), or keep the field but never read it. Removing is cleaner — update the UI client contract note in the PR body. Search for other reads of `body.user_id` in the file (PATCH etc.) and apply the same principal-binding where a write occurs.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /mnt/donna/donna && python -m pytest tests/integration/test_admin_automations.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/automations.py tests/integration/test_admin_automations.py tests/integration/conftest.py
git commit -m "fix(api): bind automation ownership to authenticated admin, not request body (IDOR)"
```

---

### Task 3: Bind sensitive host ports to loopback

`8100` (orchestrator health), `8200` (API), `3101` (browser) publish on `0.0.0.0`, so the (now-guarded) admin surface and the browser control port are reachable from any LAN host. Container-to-container traffic uses the Docker network (`donna-api:8200`, `donna-browser:3100`) and is **unaffected** by the host-side bind address; the Docker healthcheck runs inside the container. So loopback binding is safe and only removes external LAN reachability. Route external access through Caddy.

**Files:**
- Modify: `docker/donna-core.yml:36`
- Modify: `docker/donna-app.yml:20,45`

- [ ] **Step 1: Edit the port mappings**

```yaml
# docker/donna-core.yml
    ports:
      - "127.0.0.1:8100:8100"    # Health endpoint (host-local only; external via Caddy)

# docker/donna-app.yml
    ports:
      - "127.0.0.1:8200:8200"
    # ...browser service...
    ports:
      - "127.0.0.1:3101:3100"
```

- [ ] **Step 2: Validate compose still parses**

Run: `cd /mnt/donna/donna/docker && docker compose -f donna-core.yml config -q && docker compose -f donna-app.yml config -q`
Expected: no output, exit 0.

- [ ] **Step 3: Confirm Caddy reaches the API via the Docker network, not the host port**

Before applying, verify Caddy's upstream. Run: `grep -rniE "donna-api|8200|localhost:8200|127.0.0.1:8200" /mnt/donna/donna/docker /mnt/donna 2>/dev/null | grep -i caddy`
Expected: Caddy proxies to `donna-api:8200` (Docker DNS) — loopback binding does not affect it. If instead Caddy points at the host IP:8200, keep `127.0.0.1` (Caddy on the host can still reach loopback) but confirm Caddy binds the host, not a separate machine.

- [ ] **Step 4: Apply (recreates only these containers)**

Run: `cd /mnt/donna/donna/docker && docker compose -f donna-core.yml -f donna-app.yml up -d donna-orchestrator donna-api donna-browser`
Then verify: `docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E 'donna-(api|orchestrator|browser)'`
Expected: ports show `127.0.0.1:8200->8200/tcp` etc. Then `curl -sf http://127.0.0.1:8200/health` succeeds locally, and from another LAN host the port is refused.

- [ ] **Step 5: Commit**

```bash
git add docker/donna-core.yml docker/donna-app.yml
git commit -m "hardening(docker): bind health/api/browser ports to loopback; external access via Caddy"
```

---

### Task 4: Remove weak defaults and unused duplicate secret file

**Files:**
- Modify: `docker/.env.example`
- Possibly delete: `docker/google_credentials.json` (verify unused first)

- [ ] **Step 1: Confirm `docker/google_credentials.json` is unreferenced**

Run: `grep -rnE "docker/google_credentials|google_credentials.json" /mnt/donna/donna/docker /mnt/donna/donna/src`
Expected: the orchestrator/API mount `../config` (which has its own `config/google_credentials.json`); if `docker/google_credentials.json` is never mounted or read, it is a stray duplicate of the client secret and should be deleted (it is gitignored, so this only removes it from disk). If it *is* referenced, leave it and note it.

- [ ] **Step 2: Fix the weak Grafana default**

In `docker/.env.example`, change `GRAFANA_ADMIN_PASSWORD=changeme` to a clearly-invalid placeholder that forces the user to set it, and add a comment:

```
# REQUIRED: set a strong password before first boot. Do NOT ship 'changeme'.
GRAFANA_ADMIN_PASSWORD=__SET_ME__
```

- [ ] **Step 3: Commit**

```bash
git add docker/.env.example
# and, if confirmed unused: git rm --cached is N/A (untracked); just: rm docker/google_credentials.json
git commit -m "hardening(docker): force Grafana admin password; drop stray duplicate client secret"
```

---

## Deferred (documented, not executed this session)

Each has a real reason to wait; all are in the master plan's later sessions or coupled to owner-owned work:

1. **OAuth token-store encryption / read-only relocation** (audit finding #3/#5). The clean fix mounts `config/token.json` read-only from a dedicated secrets path outside the RW `config/` mount. This is coupled to the owner's in-progress **permanent Calendar OAuth fix** — do it there so the token path and refresh flow are designed together. Also rotate the client secret (`GOCSPX-…`) at that time.
2. **Browser-sidecar network isolation** (finding #2, SSRF→internal-CIDR-admin). Put `donna-browser` on an isolated Docker network / egress allowlist so it cannot reach `donna-api:8200`. Deferred because it can break the orchestrator↔browser channel and needs a live smoke test of the product-watch automations after the change.
3. **Internal-CIDR service-key gating** (finding #2). Require a mutual `service_router()` key even for internal-CIDR callers. The machinery exists; this is a policy change that also touches the UI's credential-less assumption (audit ui F4) — sequence it with a UI auth affordance.

## Execution log (2026-07-02, branch `fix/security-admin-auth`)

- **Task 1 — DONE & committed** (`d96ae95`). Six routers → `admin_router()`; invariant test added (caught 26 unguarded routes; now green, 11 passed). ruff + targeted suites pass.
- **Task 2 — DONE & committed** (`743adb5`). `create_automation` binds ownership to `CurrentAdmin`; `user_id` now server-derived; test asserts attacker `user_id` is overridden. ruff + mypy clean, 27 passed.
- **Task 3 — DONE & applied LIVE** (`508e5e7`). `donna-api:8200` and `donna-browser:3101` bound to `127.0.0.1`; containers recreated; `docker port` confirms loopback-only; `curl 127.0.0.1:8200/health` → 200; both healthy. **Decision:** left `8100` (orchestrator) on `0.0.0.0` — it hosts the signature-verified Twilio webhook that Twilio must reach externally; loopback-binding it needs a Caddy route first (tracked as follow-up). This is a deliberate deviation from the plan's original "8100/8200/3101" set.
- **Task 4 — PARTIAL & committed** (`508e5e7`). Grafana default replaced. **Decision:** did NOT delete `docker/google_credentials.json` — it is reachable via `GOOGLE_CREDENTIALS_PATH` and couples to the owner-owned Calendar OAuth work; folded into deferred item #1 instead.

### Remaining deploy step (not run — heavy, needs owner trigger)
The Task 1/2 code fix is committed and tested but **not yet live** — the running `donna-api` uses the pre-fix image. Deploying needs an image rebuild, which re-downloads PyTorch (~6 GB, ~10 min) because `Dockerfile.api:11` copies `src/` before `pip install`. The live LAN exposure is already closed by Task 3 (loopback), so the residual until rebuild is only host-localhost + homelab-container access (the latter already gets admin via the internal-CIDR path, finding #2). Deploy command when ready:

```bash
cd /mnt/donna/donna/docker && docker compose --env-file .env -f donna-app.yml build donna-api \
  && docker compose --env-file .env -f donna-app.yml up -d donna-api
# then verify: curl -sf http://127.0.0.1:8200/health && docker ps | grep donna-api
```
(Session 7 fixes the Dockerfile layer order so future rebuilds are fast.)

## Self-review

- **Coverage:** Findings #1 (Task 1), the IDOR sub-part of #1 (Task 2), #4 ports (Task 3), #6 Grafana default + #3/#5 partial (Task 4). #2 and the token-store parts of #3/#5 explicitly deferred with rationale. ✅
- **Placeholders:** none — every code/config change shows exact content. ✅
- **Type consistency:** `admin_router()` returns `APIRouter`; `CurrentAdmin` is `Annotated[str, Depends(_admin_dep)]`; `_admin_dep` is the callable asserted by the invariant test — consistent across Tasks 1–2. ✅
