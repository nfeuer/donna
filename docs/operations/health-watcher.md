# Container Health Watcher

`donna-healthwatch` is an independent sidecar that watches the Donna container
stack and posts to the Discord **debug** channel when a container goes
unhealthy/down or recovers (edge-triggered — one message per transition).

## What it watches
All containers named `donna-*` plus `caddy` (configurable). Immich and
photo-curator are intentionally excluded.

## Reciprocal monitoring
The watcher writes a heartbeat file each poll cycle to a shared volume
(`/mnt/donna/healthwatch/heartbeat`). The orchestrator reads it (read-only, no
Docker socket) and alerts to the same channel if it goes stale — so the watcher
itself is watched. The only uncovered case is both processes down at once,
which generally means the host is down.

## Configuration (`docker/.env`)
| Var | Default | Where |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | sidecar (reused from orchestrator) |
| `DISCORD_DEBUG_CHANNEL_ID` | — | sidecar |
| `HEALTHWATCH_POLL_SECONDS` | `30` | sidecar |
| `HEALTHWATCH_WATCH_PREFIX` | `donna-` | sidecar |
| `HEALTHWATCH_WATCH_EXTRA` | `caddy` | sidecar |
| `HEALTHWATCH_STALE_SECONDS` | `90` | orchestrator |

## Operating
- Logs: `docker logs donna-healthwatch`
- Restart: `docker restart donna-healthwatch`
- A `poll_failed` log line means the Docker socket was unreachable that cycle;
  the watcher skips the cycle (and does not heartbeat) and retries.
