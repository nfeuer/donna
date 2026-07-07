# Deploy Snapshot & Boot Resilience — Design

**Date:** 2026-06-23
**Status:** Approved (design), pending implementation plan
**Spec references:** `spec_v3.md` §3.5 Infrastructure & Deployment, §3.5.1 Docker Compose Structure (this design adds a new §3.5.3 Snapshot Deploy & Boot Resilience — see "Spec Updates" below).

## 1. Context & Problem

On 2026-06-22 the host running the Donna stack was physically moved and power-cycled. After boot, the `donna-orchestrator` container entered a crash loop (1,075 restarts over ~18h, exit 1) with:

```
FileNotFoundError: '/donna/config/donna_models.yaml'
```

### Root cause

The orchestrator bind-mounts its config/prompts/schemas from a **deploy snapshot directory**, not from the git checkout:

- Container mounts: `/mnt/donna/deploy-main/config → /donna/config`, `…/prompts → /app/prompts`, `…/schemas → /app/schemas`.
- The running compose project is named `docker`, composed from `/mnt/donna/deploy-main/docker/*.yml` (files: `donna-core.yml`, `donna-app.yml`, `donna-ui.yml`, `donna-monitoring.yml`, `donna-ollama.yml`). The relative volume `../config` in `donna-core.yml` therefore resolves to `/mnt/donna/deploy-main/config`.
- After the move, `deploy-main` lost its contents. When Docker auto-started the `unless-stopped` containers on boot, it **silently created the missing bind-mount sources as empty root-owned directories**, so the mount succeeded but the config file was absent.

### Two failure factors

1. **The snapshot was not repeatable.** It was built by hand with no committed script. After the move there was nothing to re-run, and diagnosis required filesystem archaeology.
2. **The failure was silent.** Docker's empty-dir stubbing + `unless-stopped` produced an invisible 3-day crash loop. `donna-healthwatch` was running but did not alert.

### Pre-existing drift (discovered during design)

The committed helper `scripts/donna-up.sh` mounts the **repo directly** (`$PROJECT_DIR/docker`, `COMPOSE_PROJECT_NAME=donna`) — the *opposite* of the running stack (`deploy-main`, project `docker`). The snapshot deployment is an undocumented, ad-hoc state that diverges from version control. This design reconciles the two.

## 2. Goals / Non-Goals

**Goals**

- A reboot or a machine move brings Donna back up healthy with **zero manual intervention**.
- The deploy is a **committed, repeatable** process — recovery from snapshot loss is one command (or automatic).
- Snapshot loss or missing required config **fails loud** (alert + structured log), never a silent crash loop.
- Running reality matches version control (reconcile `donna-up.sh` and the live `deploy-main` state).

**Non-Goals**

- Reworking the multi-file compose pattern itself (§3.5.1 stays as-is).
- Moving secrets into a vault/secret manager (noted as a follow-up).
- A general orchestrator-internal "config missing" guard (prevention #2 — tracked as a follow-up; this design makes the failure loud at the deploy/boot layer instead).

## 3. Why keep the snapshot (vs. mounting the repo directly)

`/mnt/donna/donna` is an **active development + agent workspace** (live git checkout, `.claude/worktrees/` with agents editing files, frequently mid-edit or on a feature branch). Mounting it directly into the production stack means any container restart or reboot picks up whatever happens to be on disk at that instant. The snapshot decouples the always-on production stack from the dev workspace: production only changes on a deliberate deploy. This is worth keeping for a solo homelab where the same directory is both IDE and source of truth.

## 4. Design

### 4.1 `scripts/donna-deploy.sh` (committed — single source of truth)

An idempotent script with subcommands. The snapshot is always built from **committed** state (`git archive`), never the dirty working tree.

- **`snapshot`** — build a fresh snapshot:
  1. `git -C <repo> archive <ref> config prompts schemas docker | tar -x -C <staging>` where `<ref>` defaults to `HEAD`. (Archives committed content only — a dirty/mid-edit working tree cannot leak into production.)
  2. **Overlay secrets** (gitignored, not in the archive) into the staging tree: `docker/.env`, `docker/google_credentials.json`, `config/google_credentials.json`, `config/token.json`. Source: the repo working tree (see §6 follow-up for vault).
  3. **Validate** required files exist in staging (at minimum `config/donna_models.yaml`, `docker/.env`, and each compose file). Abort if any are missing — never publish a partial snapshot.
  4. **Atomic swap**: move the validated staging tree into place at `/mnt/donna/deploy-main` (build at `deploy-main.staging`, then `mv` old aside / `mv` staging into place). `deploy-main` is therefore only ever the previous valid snapshot or the new one — Docker can never observe it empty.
  5. Write `/mnt/donna/deploy-main/.deployed-sha` with the resolved commit SHA.
- **`up`** — `docker compose` the stack from `deploy-main/docker` with the canonical project name and `--env-file deploy-main/docker/.env` (see §5 for the project-name cutover).
- **`deploy`** = `snapshot` + `up`. This is what the operator runs to ship changes.
- **`ensure`** (boot mode) — if `deploy-main` is valid (required files present) → `up` only. If missing/empty/invalid → rebuild via `snapshot` using the SHA in `.deployed-sha` (fallback `HEAD`), then `up`. Any rebuild or validation failure triggers an alert (§4.3).

### 4.2 `systemd/donna.service` (boot self-heal)

```
[Unit]
Description=Donna stack ensure (snapshot + compose up)
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/mnt/donna/donna/scripts/donna-deploy.sh ensure
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

Enabled so every boot runs `ensure`. Containers keep `unless-stopped` (Docker still auto-starts them); the unit is what makes a **snapshot loss** — not just a clean reboot — self-heal. Even if Docker starts a container against a transiently-missing snapshot first, `ensure` rebuilds the snapshot within seconds and the next container restart succeeds.

### 4.3 Fail-loud alerting

`ensure` (and `snapshot` validation) post to a configurable `DONNA_ALERT_WEBHOOK` (Discord debug channel) **and** emit a structured log line whenever:

- the snapshot was missing and had to be rebuilt, or
- a required file is absent / validation fails.

This is self-contained at the deploy/boot layer and does **not** depend on the orchestrator's own config loading (which is the thing that may be broken). It closes the gap that hid the outage for three days.

## 5. Reconciliation & Cutover

- **`donna-deploy.sh` becomes the canonical deploy path.** `scripts/donna-up.sh` / `donna-down.sh` are updated to operate on the snapshot (`deploy-main/docker`) with the canonical project name, or retired in favour of `donna-deploy.sh` to remove the contradiction. (Decision deferred to the implementation plan; either way they must stop contradicting the running model.)
- **Project name:** the live containers run under project `docker`. The cutover must **keep project name `docker`** (set `COMPOSE_PROJECT_NAME=docker` / run from the `deploy-main/docker` dir) so re-running `up` adopts the existing containers rather than spawning a duplicate `donna` project. Switching the canonical name is possible but requires a one-time controlled `down` of the old project then `up` of the new — called out as an explicit, supervised migration step, not part of routine `ensure`.
- **Ownership:** the systemd unit runs as root and owns `deploy-main`; the atomic-swap build replaces today's ad-hoc root-owned empty stubs.

## 6. Testing

- **Unit/script tests** (bats or pytest-driven shell): `snapshot` produces a tree containing `config/donna_models.yaml` + secrets; validation aborts when a required file is removed; atomic swap leaves `deploy-main` valid even if the build is interrupted (no empty-dir window).
- **Self-heal integration test** (manual/scripted on host): delete `deploy-main`, run `donna-deploy.sh ensure`, assert the orchestrator reaches `healthy` and an alert fired.
- **Reboot drill:** reboot the host, assert the stack returns healthy with no manual step.
- **Dirty-tree isolation:** make an uncommitted edit to `config/donna_models.yaml`, run `deploy`, assert the snapshot reflects `HEAD` (committed) content, not the edit.

## 7. Spec Updates

Add **§3.5.3 Snapshot Deploy & Boot Resilience** to `spec_v3.md`: the production stack runs from a committed, validated snapshot at `/mnt/donna/deploy-main`; `scripts/donna-deploy.sh` is the deploy entry point; a oneshot systemd unit self-heals the snapshot on boot; snapshot loss / missing required config alerts via `DONNA_ALERT_WEBHOOK`. Cross-link from `docs/operations/docker.md` and add `docs/operations/deployment.md`.

## 8. Follow-ups (append to `docs/superpowers/specs/followups.md`)

- **Secrets source:** move the overlaid secrets (`.env`, `google_credentials.json`, `token.json`) out of the dev working tree into a dedicated secrets dir or `/mnt/donna/vault`, so `snapshot` does not read secrets from the IDE workspace.
- **Healthwatch alert gap:** `donna-healthwatch` was running during the outage but did not page on a >18h orchestrator crash loop. Investigate and close.
- **Prevention #2 (orchestrator guard):** the orchestrator could still detect missing config at startup and emit a notification before exiting, as defense-in-depth behind the deploy-layer guard.
