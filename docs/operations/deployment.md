# Deployment

Design reference: [`spec_v3.md` §3.5.3 Snapshot Deploy & Boot Resilience](../reference-specs/spec-v3.md)
and [design doc](../superpowers/specs/2026-06-23-deploy-snapshot-resilience-design.md).

The production stack runs from a **committed snapshot** at `/mnt/donna/deploy-main`,
not the live git checkout. The snapshot is frozen committed state plus gitignored
secrets, so editing the repo never affects the running stack until you deploy.

## Commands (`scripts/donna-deploy.sh`)

| Command   | Action |
|-----------|--------|
| `deploy`  | Build a fresh snapshot (config/prompts/schemas) and bring the stack up, reusing the current orchestrator image. Code/dependency changes need an image rebuild (see note below). |
| `snapshot`| Build/validate the snapshot only. |
| `up`      | `docker compose up -d` from the existing snapshot (project `docker`). |
| `ensure`  | Boot mode: rebuild the snapshot if missing/invalid, then `up`. |

Required files (`config/donna_models.yaml`, `docker/.env`, `docker/donna-core.yml`)
are validated before publish; a partial snapshot is never swapped in.

After a snapshot swap, `deploy` (and `ensure` when it rebuilds) **restarts** the
running containers whose bind mount is under `deploy-main` so they re-resolve the
mount and reload config. The atomic swap replaces the directory inode, so an
already-running container otherwise keeps a dangling mount and serves stale
config until restarted. Restart is used (never `--force-recreate`) so no new
containers or orphans are created and services that don't mount the snapshot
(e.g. the Ollama GPU model) are left untouched. `up` also runs with
`--remove-orphans` to keep stale containers from accumulating.

## Shipping code changes

The snapshot archives `config/`, `prompts/`, `schemas/`, and `docker/` — it does
**not** include the image build context (`src/`, `pyproject.toml`, `alembic/`).
Therefore `deploy` ships config/prompt/schema changes and reuses the already-built
orchestrator image; it does **not** rebuild application code or install new
dependencies.

To ship code changes, rebuild the orchestrator image explicitly:

```bash
docker compose -f /mnt/donna/deploy-main/docker/donna-core.yml \
  --project-name docker up -d --build
```

A follow-up is tracked to decide whether snapshots should become fully
self-sufficient by including the image build context (see
`docs/superpowers/specs/followups.md` — "Self-sufficient snapshots for fresh
hardware").

## Boot self-heal

`systemd/donna.service` (oneshot, after `docker.service`) runs `ensure` on every
boot. A missing snapshot (e.g. after a machine move) is rebuilt automatically and
an alert is posted to `DONNA_ALERT_WEBHOOK`. The unit reads that value from a
root-only env file that is kept out of git — create `/etc/donna/deploy.env`
(mode `0600`) containing `DONNA_ALERT_WEBHOOK=<discord-webhook-url>` before
enabling the unit, otherwise boot alerts are silent.

## Secrets

Gitignored secrets (`docker/.env`, `config/google_credentials.json`,
`config/token.json`, `docker/google_credentials.json`) are overlaid from the repo
working tree during `snapshot`. See follow-up to relocate these to a vault.
