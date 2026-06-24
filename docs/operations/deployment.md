# Deployment

Design reference: [`spec_v3.md` §3.5.3 Snapshot Deploy & Boot Resilience](../reference-specs/spec-v3.md)
and [design doc](../superpowers/specs/2026-06-23-deploy-snapshot-resilience-design.md).

The production stack runs from a **committed snapshot** at `/mnt/donna/deploy-main`,
not the live git checkout. The snapshot is frozen committed state plus gitignored
secrets, so editing the repo never affects the running stack until you deploy.

## Commands (`scripts/donna-deploy.sh`)

| Command   | Action |
|-----------|--------|
| `deploy`  | Build a fresh snapshot from `HEAD` and bring the stack up. Run this to ship. |
| `snapshot`| Build/validate the snapshot only. |
| `up`      | `docker compose up -d` from the existing snapshot (project `docker`). |
| `ensure`  | Boot mode: rebuild the snapshot if missing/invalid, then `up`. |

Required files (`config/donna_models.yaml`, `docker/.env`, `docker/donna-core.yml`)
are validated before publish; a partial snapshot is never swapped in.

## Boot self-heal

`systemd/donna.service` (oneshot, after `docker.service`) runs `ensure` on every
boot. A missing snapshot (e.g. after a machine move) is rebuilt automatically and
an alert is posted to `DONNA_ALERT_WEBHOOK`.

## Secrets

Gitignored secrets (`docker/.env`, `config/google_credentials.json`,
`config/token.json`, `docker/google_credentials.json`) are overlaid from the repo
working tree during `snapshot`. See follow-up to relocate these to a vault.
