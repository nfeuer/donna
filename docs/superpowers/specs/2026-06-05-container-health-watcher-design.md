# Container Health Watcher — Design

**Date:** 2026-06-05
**Status:** Approved (brainstorming)

## Problem

Containers in the Donna homelab stack can go `unhealthy` or stop without anyone
noticing. This happened concretely: after a host reboot, `donna-orchestrator`
crash-looped on an alembic migration mismatch and sat `unhealthy` for ~44 hours
before it was caught. There is no proactive signal when a container's health
degrades — you have to remember to run `docker ps`.

## Goal

A watcher that posts to the Discord **debug/bug channel** when a watched
container becomes unhealthy or stops, and again when it recovers — quietly
(one message per real transition), and reliably enough to have caught the
orchestrator outage above.

## Key Decisions

| Decision | Choice | Why |
|---|---|---|
| Where it runs | **Independent sidecar container** | The motivating outage was the orchestrator itself; a watcher inside it could not have alerted. Independence is the whole point. |
| Scope | All `donna-*` containers **+ `caddy`** | The app stack you actively develop. Immich/photo-curator excluded as noise. |
| Trigger | **Edge-triggered + recovery** | One ping when a container goes bad, one when it recovers. No repeats while unchanged. |
| Discord transport | **Existing bot token + `DISCORD_DEBUG_CHANNEL_ID`** via REST | Reuses configured credentials/channel; no new webhook to manage. |
| Watching the watcher | **Heartbeat file + orchestrator check** | Reciprocal monitoring closes the "who watches the watchdog" gap, catches hangs, and keeps the Docker socket off the LLM-facing orchestrator. |
| Code form | **Standalone stdlib-only script in its own tiny image** | Hard independence from the app; fast restarts; no shared dependency failures. |

## Architecture

```
                 reads (ro)                       posts
 docker.sock  ───────────────►  donna-healthwatch ───────► Discord debug channel
   (engine)                       │  (sidecar)
                                  │ writes heartbeat each cycle
                                  ▼
                       /mnt/donna/healthwatch/heartbeat   (shared host dir)
                                  ▲
                                  │ reads (ro), checks freshness
                       donna-orchestrator scheduler task ─► Discord debug channel
                                                            (only if heartbeat stale)
```

Two independent processes monitor each other:

- **`donna-healthwatch`** watches every container (incl. the orchestrator) and
  alerts on health transitions. Each cycle it writes a heartbeat file.
- **`donna-orchestrator`** runs one scheduler task that alerts if the heartbeat
  goes stale — i.e. if the watchdog has died or hung.

The only uncovered case is both dying simultaneously, which generally means the
host is down (independently noticeable). Accepted.

## Component 1 — `donna-healthwatch` sidecar

**Image:** `docker/Dockerfile.healthwatch`, `FROM python:3.12-slim`, copies a
single stdlib-only script. Not the orchestrator image.

**Compose:** new `donna-healthwatch` service in `docker/donna-monitoring.yml`:
- `restart: unless-stopped`, **no `depends_on`**.
- Mounts:
  - `/var/run/docker.sock:/var/run/docker.sock:ro`
  - `/mnt/donna/healthwatch:/var/run/healthwatch` (rw — heartbeat output)
- Env (from `docker/.env`): `DISCORD_BOT_TOKEN`, `DISCORD_DEBUG_CHANNEL_ID`,
  and optional `HEALTHWATCH_POLL_SECONDS` (default `30`),
  `HEALTHWATCH_WATCH_PREFIX` (default `donna-`),
  `HEALTHWATCH_WATCH_EXTRA` (default `caddy`),
  `HEALTHWATCH_HEARTBEAT_PATH` (default `/var/run/healthwatch/heartbeat`).

**Script** `docker/healthwatch/healthwatch.py` — pure-function core for testing:

- `classify(record) -> Status` — maps a Docker container record to:
  - `OK` — running and (healthy OR no healthcheck defined)
  - `UNHEALTHY` — running with health status `unhealthy`
  - `DOWN` — state `exited` / `restarting` / `dead` / `paused` / `created`
  - `MISSING` — a watched name with no matching container
- `diff(prev, cur) -> [Event]` — `bad` on `OK`→non-`OK` (and first-seen-bad),
  `recovered` on non-`OK`→`OK`, nothing when unchanged.
- `poll(http_over_socket) -> {name: Status}` — `GET /containers/json?all=1`,
  read `State`/`Status`/health for each, restrict to the watch set.
- `notify(event) -> bool` — `POST https://discord.com/api/v10/channels/{id}/messages`
  with `Authorization: Bot <token>`; returns success. Honors 429 `retry-after`.
- `main()` — loop every `HEALTHWATCH_POLL_SECONDS`: `poll` → `diff` vs stored
  state → `notify` each event → on success update stored state → write heartbeat.

**Watch-set resolution:** each cycle expand `name startswith HEALTHWATCH_WATCH_PREFIX`
∪ `split(HEALTHWATCH_WATCH_EXTRA)` against the live container list — new matching
containers are auto-watched; a vanished watched name becomes `MISSING`.

**Startup:** stored state starts empty. Anything already bad on the first cycle
emits one `bad` event, so restarting the watcher resurfaces current problems
rather than going blind to a pre-existing outage.

**Heartbeat:** after each successful poll cycle (regardless of whether any event
fired), write the current UTC ISO-8601 timestamp to `HEALTHWATCH_HEARTBEAT_PATH`
(atomic write: temp file + rename). A skipped cycle (socket error) does **not**
update the heartbeat — so a stuck watchdog goes stale and the orchestrator
notices.

**Message format** (host + timestamp footer on each):
- `🔴 **donna-orchestrator** is UNHEALTHY (was OK) — Up 44 hours (unhealthy)`
- `🔴 **caddy** is DOWN (exited, code 1)`
- `🟢 **donna-orchestrator** recovered — healthy`

## Component 2 — orchestrator heartbeat monitor

**Module:** `src/donna/healthwatch/heartbeat_monitor.py`
- `is_stale(now, heartbeat_mtime, threshold_seconds) -> bool` — pure.
- A small transition tracker so the orchestrator alerts **once** when the
  heartbeat goes stale and once when it recovers (same edge-trigger discipline).

**Wiring:** register a scheduler task alongside the existing background tasks
(reminder scheduler, overdue detector, digests) in the orchestrator startup.
- Reads `HEALTHWATCH_HEARTBEAT_PATH` (orchestrator-side default
  `/donna/healthwatch/heartbeat`).
- Interval: 60 s. Stale threshold: `HEALTHWATCH_STALE_SECONDS` (default `90`,
  i.e. 3× the 30 s watchdog poll).
- On stale→ alert via the existing Discord debug-channel notifier:
  `🟠 **donna-healthwatch** heartbeat stale (last seen 2m ago) — the watcher may be down`.
  On recovery: `🟢 donna-healthwatch heartbeat resumed`.

**Compose change:** mount the shared dir read-only into the orchestrator:
`/mnt/donna/healthwatch:/donna/healthwatch:ro`, and pass `HEALTHWATCH_STALE_SECONDS`.

## Error Handling

- **Docker socket error (watchdog):** log to stderr, skip the cycle, retry next.
  Never crash; never advance state; never write the heartbeat. `restart:
  unless-stopped` covers hard crashes.
- **Discord send failure (watchdog or orchestrator):** log; do **not** advance
  the affected container's stored state, so it retries next cycle (at-least-once).
  429 → sleep `retry-after`, continue.
- **Heartbeat file missing/unreadable (orchestrator):** treat as stale (alert),
  unless the watcher has genuinely never started — covered, since the watchdog
  writes on its first successful cycle.

## Testing

- `classify` — synthetic Docker records: running+healthy, running+unhealthy,
  running+no-healthcheck, exited, restarting, paused, missing → expected status.
- `diff` — every transition incl. first-seen-bad and no-change → expected events.
- `notify` — monkeypatched HTTP call asserting Discord URL, `Bot` auth header,
  and message body; no real network.
- `is_stale` / orchestrator transition tracker — fresh vs stale vs recovered.
- Optional, marked: smoke test that lists containers over the real socket
  read-only (skipped in CI without a socket).

## Files

- `docker/healthwatch/healthwatch.py` (script + importable pure functions)
- `docker/Dockerfile.healthwatch`
- `docker/donna-monitoring.yml` (+ `donna-healthwatch`; orchestrator mount/env
  lives in its existing compose file, `docker/donna-core.yml`)
- `src/donna/healthwatch/heartbeat_monitor.py` + scheduler wiring
- `tests/unit/healthwatch/test_classify.py`, `test_diff.py`, `test_notify.py`,
  `test_heartbeat_monitor.py`
- Short note in the monitoring docs + the new `HEALTHWATCH_*` env vars

## Out of Scope (v1)

- Both watcher and orchestrator down simultaneously (host-down case).
- Per-container thresholds, flap-damping windows, or escalation policies.
- Watching non-Donna containers (immich/photo-curator).
