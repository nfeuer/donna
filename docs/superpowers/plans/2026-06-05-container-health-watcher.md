# Container Health Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent `donna-healthwatch` sidecar that posts Discord debug-channel alerts when a watched container goes unhealthy/down or recovers, plus a reciprocal orchestrator task that alerts if the watchdog's heartbeat goes stale.

**Architecture:** A standalone stdlib-only Python script runs in its own tiny container, polls the Docker Engine API over a read-only socket mount, edge-triggers Discord messages on health transitions, and writes a heartbeat file each cycle. The orchestrator reads that heartbeat (read-only shared volume, no Docker socket) and alerts to the same debug channel if it goes stale — so each process covers the other's death.

**Tech Stack:** Python 3.12 stdlib (`http.client` over `AF_UNIX`, `urllib.request`), Docker Compose, Discord REST API, pytest. Design spec: `docs/superpowers/specs/2026-06-05-container-health-watcher-design.md`. Relevant `spec_v3.md` area: observability/notifications.

---

## File Structure

**Watchdog sidecar (standalone, NOT part of the app image):**
- `docker/healthwatch/healthwatch.py` — script + importable pure functions (`classify`, `diff`, `poll`, `notify`, `main`). Deliberately stdlib-only and synchronous so it shares no dependency or async-runtime failure modes with the orchestrator. The repo's async/structlog conventions intentionally do **not** apply here.
- `docker/Dockerfile.healthwatch` — `python:3.12-slim` image carrying just the script.
- `docker/donna-monitoring.yml` — add the `donna-healthwatch` service.

**Orchestrator side (app code — full repo conventions apply):**
- `src/donna/healthwatch/__init__.py`
- `src/donna/healthwatch/heartbeat_monitor.py` — `is_stale()` + `HeartbeatMonitor`.
- `src/donna/server.py` — wire `HeartbeatMonitor` into the background-task list.
- `docker/donna-core.yml` — mount the shared heartbeat dir read-only + pass env.

**Tests:**
- `tests/unit/healthwatch/__init__.py`
- `tests/unit/healthwatch/conftest.py` — put `docker/healthwatch` on `sys.path` so the sidecar script is importable.
- `tests/unit/healthwatch/test_classify.py`, `test_diff.py`, `test_notify.py`, `test_poll.py`
- `tests/unit/healthwatch/test_heartbeat_monitor.py`

**Docs:**
- `docs/operations/health-watcher.md` (new narrative page)
- `docker/.env.example` (or the env template) — document `HEALTHWATCH_*`
- `docs/superpowers/specs/followups.md` — append a spec-drift note.

---

## Task 1: Watchdog `classify()` — container record → status

**Files:**
- Create: `docker/healthwatch/healthwatch.py`
- Create: `tests/unit/healthwatch/__init__.py` (empty)
- Create: `tests/unit/healthwatch/conftest.py`
- Test: `tests/unit/healthwatch/test_classify.py`

- [ ] **Step 1: Add conftest so the sidecar script is importable**

Create `tests/unit/healthwatch/conftest.py`:

```python
import sys
from pathlib import Path

# The watchdog lives under docker/ (its own image), not src/. Put it on the
# path so unit tests can import its pure functions without Docker.
_HEALTHWATCH_DIR = Path(__file__).resolve().parents[3] / "docker" / "healthwatch"
if str(_HEALTHWATCH_DIR) not in sys.path:
    sys.path.insert(0, str(_HEALTHWATCH_DIR))
```

Create empty `tests/unit/healthwatch/__init__.py`.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/healthwatch/test_classify.py`:

```python
import healthwatch as hw


def rec(name, state, health=None):
    return {"name": name, "state": state, "health": health}


def test_running_healthy_is_ok():
    assert hw.classify(rec("donna-api", "running", "healthy")) == hw.OK


def test_running_no_healthcheck_is_ok():
    assert hw.classify(rec("caddy", "running", None)) == hw.OK


def test_running_starting_is_ok():
    # health:starting during boot must not false-alarm
    assert hw.classify(rec("donna-orchestrator", "running", "starting")) == hw.OK


def test_running_unhealthy():
    assert hw.classify(rec("donna-orchestrator", "running", "unhealthy")) == hw.UNHEALTHY


def test_exited_is_down():
    assert hw.classify(rec("caddy", "exited", None)) == hw.DOWN


def test_restarting_is_down():
    assert hw.classify(rec("donna-ui", "restarting", None)) == hw.DOWN


def test_paused_and_created_and_dead_are_down():
    for s in ("paused", "created", "dead"):
        assert hw.classify(rec("x", s, None)) == hw.DOWN
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/healthwatch/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'healthwatch'`.

- [ ] **Step 4: Write minimal implementation**

Create `docker/healthwatch/healthwatch.py`:

```python
#!/usr/bin/env python3
"""donna-healthwatch: standalone container health watcher.

Polls the Docker Engine API over a read-only socket, posts Discord debug-channel
alerts on container health transitions, and writes a heartbeat each cycle.

Stdlib-only and synchronous on purpose: this process must share no dependency or
runtime failure modes with the orchestrator it watches.
"""
from __future__ import annotations

# --- Status constants -------------------------------------------------------
OK = "OK"
UNHEALTHY = "UNHEALTHY"
DOWN = "DOWN"
MISSING = "MISSING"

_RUNNING_BAD_HEALTH = {"unhealthy"}


def classify(record: dict) -> str:
    """Map a normalized container record to a status constant.

    Args:
        record: ``{"name": str, "state": str, "health": str | None}`` where
            ``state`` is a Docker state (running/exited/...) and ``health`` is
            ``healthy``/``unhealthy``/``starting``/``None``.

    Returns:
        One of ``OK``, ``UNHEALTHY``, ``DOWN``.
    """
    state = record.get("state")
    if state == "running":
        if record.get("health") in _RUNNING_BAD_HEALTH:
            return UNHEALTHY
        return OK
    return DOWN
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/healthwatch/test_classify.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add docker/healthwatch/healthwatch.py tests/unit/healthwatch/
git commit -m "feat(healthwatch): classify container records into health statuses"
```

---

## Task 2: Watchdog `diff()` — edge-triggered transition events

**Files:**
- Modify: `docker/healthwatch/healthwatch.py`
- Test: `tests/unit/healthwatch/test_diff.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/healthwatch/test_diff.py`:

```python
import healthwatch as hw


def test_first_seen_bad_emits_bad():
    events = hw.diff({}, {"donna-api": hw.UNHEALTHY})
    assert events == [hw.Event("donna-api", "bad", hw.UNHEALTHY)]


def test_first_seen_ok_emits_nothing():
    assert hw.diff({}, {"donna-api": hw.OK}) == []


def test_ok_to_bad_emits_bad():
    events = hw.diff({"caddy": hw.OK}, {"caddy": hw.DOWN})
    assert events == [hw.Event("caddy", "bad", hw.DOWN)]


def test_bad_to_ok_emits_recovered():
    events = hw.diff({"caddy": hw.DOWN}, {"caddy": hw.OK})
    assert events == [hw.Event("caddy", "recovered", hw.OK)]


def test_unchanged_bad_emits_nothing():
    assert hw.diff({"caddy": hw.DOWN}, {"caddy": hw.DOWN}) == []


def test_bad_to_different_bad_emits_nothing():
    # Still bad; we already alerted. No second ping on UNHEALTHY->DOWN.
    assert hw.diff({"x": hw.UNHEALTHY}, {"x": hw.DOWN}) == []


def test_missing_is_bad():
    events = hw.diff({"donna-api": hw.OK}, {"donna-api": hw.MISSING})
    assert events == [hw.Event("donna-api", "bad", hw.MISSING)]


def test_events_sorted_by_name_for_determinism():
    cur = {"b": hw.DOWN, "a": hw.DOWN}
    names = [e.name for e in hw.diff({}, cur)]
    assert names == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/healthwatch/test_diff.py -v`
Expected: FAIL — `AttributeError: module 'healthwatch' has no attribute 'Event'`.

- [ ] **Step 3: Write minimal implementation**

Add to `docker/healthwatch/healthwatch.py` (after the status constants):

```python
from typing import NamedTuple


class Event(NamedTuple):
    name: str
    kind: str   # "bad" | "recovered"
    status: str


def _is_ok(status: str) -> bool:
    return status == OK


def diff(prev: dict[str, str], cur: dict[str, str]) -> list[Event]:
    """Compute edge-triggered transition events between two status maps.

    Emits ``bad`` when a container moves OK->not-OK (or is first seen not-OK),
    ``recovered`` when not-OK->OK. No event while a status is unchanged or moves
    between two non-OK states. Events are sorted by name for deterministic output.
    """
    events: list[Event] = []
    for name in sorted(cur):
        new = cur[name]
        old = prev.get(name)
        was_ok = old is None or _is_ok(old)
        now_ok = _is_ok(new)
        if was_ok and not now_ok:
            events.append(Event(name, "bad", new))
        elif old is not None and not _is_ok(old) and now_ok:
            events.append(Event(name, "recovered", new))
    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/healthwatch/test_diff.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add docker/healthwatch/healthwatch.py tests/unit/healthwatch/test_diff.py
git commit -m "feat(healthwatch): edge-triggered diff of status maps into events"
```

---

## Task 3: Watchdog `notify()` — post an event to Discord

**Files:**
- Modify: `docker/healthwatch/healthwatch.py`
- Test: `tests/unit/healthwatch/test_notify.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/healthwatch/test_notify.py`:

```python
import healthwatch as hw


class _FakePoster:
    def __init__(self, status=204):
        self.calls = []
        self._status = status

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self._status, ""


def test_format_message_bad_unhealthy():
    msg = hw.format_message(hw.Event("donna-orchestrator", "bad", hw.UNHEALTHY), host="box")
    assert "donna-orchestrator" in msg
    assert "UNHEALTHY" in msg
    assert msg.startswith("🔴")


def test_format_message_recovered():
    msg = hw.format_message(hw.Event("caddy", "recovered", hw.OK), host="box")
    assert msg.startswith("🟢")
    assert "recovered" in msg.lower()


def test_notify_posts_to_channel_with_bot_auth():
    poster = _FakePoster(status=204)
    ok = hw.notify(
        hw.Event("caddy", "bad", hw.DOWN),
        channel_id="123",
        token="secrettoken",
        host="box",
        poster=poster,
    )
    assert ok is True
    url, headers, body = poster.calls[0]
    assert url == "https://discord.com/api/v10/channels/123/messages"
    assert headers["Authorization"] == "Bot secrettoken"
    assert "caddy" in body


def test_notify_returns_false_on_http_error():
    poster = _FakePoster(status=500)
    assert hw.notify(
        hw.Event("caddy", "bad", hw.DOWN),
        channel_id="123", token="t", host="box", poster=poster,
    ) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/healthwatch/test_notify.py -v`
Expected: FAIL — `AttributeError: module 'healthwatch' has no attribute 'format_message'`.

- [ ] **Step 3: Write minimal implementation**

Add to `docker/healthwatch/healthwatch.py`:

```python
import json
import time
import urllib.request
from datetime import UTC, datetime

_DISCORD_API = "https://discord.com/api/v10"


def format_message(event: Event, host: str) -> str:
    """Render a Discord message body for a transition event."""
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    if event.kind == "recovered":
        head = f"🟢 **{event.name}** recovered — {event.status}"
    else:
        head = f"🔴 **{event.name}** is {event.status}"
    return f"{head}\n`{host}` · {ts}"


def _http_post(url: str, headers: dict[str, str], body: str) -> tuple[int, str]:
    """Default poster: POST JSON via urllib. Returns (status_code, text)."""
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        return exc.code, exc.read().decode("utf-8", "replace")


def notify(
    event: Event,
    channel_id: str,
    token: str,
    host: str,
    poster=_http_post,
) -> bool:
    """Post a transition event to the Discord channel. Returns success.

    Retries once on HTTP 429, honoring ``retry-after`` from the JSON body.
    """
    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps({"content": format_message(event, host)})
    status, text = poster(url, headers, body)
    if status == 429:
        retry_after = 1.0
        try:
            retry_after = float(json.loads(text).get("retry_after", 1.0))
        except (ValueError, TypeError):
            pass
        time.sleep(min(retry_after, 30.0))
        status, text = poster(url, headers, body)
    return 200 <= status < 300
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/healthwatch/test_notify.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add docker/healthwatch/healthwatch.py tests/unit/healthwatch/test_notify.py
git commit -m "feat(healthwatch): notify() posts transition events to Discord"
```

---

## Task 4: Watchdog `poll()` — Docker API → status map

**Files:**
- Modify: `docker/healthwatch/healthwatch.py`
- Test: `tests/unit/healthwatch/test_poll.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/healthwatch/test_poll.py`:

```python
import healthwatch as hw

# Minimal shapes from GET /containers/json?all=1 and /containers/{id}/json.
LIST = [
    {"Id": "1", "Names": ["/donna-orchestrator"], "State": "running",
     "Status": "Up 2 hours (healthy)"},
    {"Id": "2", "Names": ["/caddy"], "State": "exited",
     "Status": "Exited (1) 3 minutes ago"},
    {"Id": "3", "Names": ["/immich-server"], "State": "running",
     "Status": "Up 2 hours (healthy)"},
    {"Id": "4", "Names": ["/donna-api"], "State": "running",
     "Status": "Up 2 hours (unhealthy)"},
]


def fake_fetch(path):
    assert path == "/containers/json?all=1"
    return LIST


def test_watch_set_matches_prefix_and_extras():
    names = hw.resolve_watch_set(LIST, prefix="donna-", extras=["caddy"])
    assert names == {"donna-orchestrator", "caddy", "donna-api"}


def test_poll_builds_status_map_for_watched_only():
    status = hw.poll(fake_fetch, prefix="donna-", extras=["caddy"])
    assert status == {
        "donna-orchestrator": hw.OK,
        "donna-api": hw.UNHEALTHY,
        "caddy": hw.DOWN,
    }
    assert "immich-server" not in status


def test_poll_marks_configured_extra_missing_when_absent():
    status = hw.poll(fake_fetch, prefix="donna-", extras=["caddy", "ghost"])
    assert status["ghost"] == hw.MISSING


def test_parse_health_from_status_string():
    assert hw._health_from_status("Up 2 hours (healthy)") == "healthy"
    assert hw._health_from_status("Up 2 hours (unhealthy)") == "unhealthy"
    assert hw._health_from_status("Up 2 hours (health: starting)") == "starting"
    assert hw._health_from_status("Up 2 hours") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/healthwatch/test_poll.py -v`
Expected: FAIL — `AttributeError: module 'healthwatch' has no attribute 'resolve_watch_set'`.

- [ ] **Step 3: Write minimal implementation**

Add to `docker/healthwatch/healthwatch.py`:

```python
import re

_HEALTH_RE = re.compile(r"\((?:health: )?(healthy|unhealthy|starting)\)")


def _name_of(container: dict) -> str:
    names = container.get("Names") or [""]
    return names[0].lstrip("/")


def _health_from_status(status_text: str) -> str | None:
    """Extract health from the Docker ``Status`` string, or None if absent."""
    match = _HEALTH_RE.search(status_text or "")
    return match.group(1) if match else None


def resolve_watch_set(
    containers: list[dict], prefix: str, extras: list[str]
) -> set[str]:
    """Names to watch: anything starting with prefix, plus configured extras."""
    watched = {n for c in containers if (n := _name_of(c)).startswith(prefix)}
    watched.update(e for e in extras if e)
    return watched


def poll(fetch, prefix: str, extras: list[str]) -> dict[str, str]:
    """Query Docker and return ``{name: status}`` for the watch set.

    Args:
        fetch: ``Callable[[str], list[dict]]`` returning parsed JSON for a path.
            Injected for testability; production passes a Docker-socket fetcher.
        prefix: container-name prefix to watch (e.g. ``donna-``).
        extras: explicit extra names to watch (e.g. ``["caddy"]``).
    """
    containers = fetch("/containers/json?all=1")
    by_name = {_name_of(c): c for c in containers}
    watched = resolve_watch_set(containers, prefix, extras)
    result: dict[str, str] = {}
    for name in watched:
        container = by_name.get(name)
        if container is None:
            result[name] = MISSING
            continue
        record = {
            "name": name,
            "state": container.get("State"),
            "health": _health_from_status(container.get("Status", "")),
        }
        result[name] = classify(record)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/healthwatch/test_poll.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add docker/healthwatch/healthwatch.py tests/unit/healthwatch/test_poll.py
git commit -m "feat(healthwatch): poll Docker API into a watched status map"
```

---

## Task 5: Watchdog socket fetcher, heartbeat write, and `main()` loop

**Files:**
- Modify: `docker/healthwatch/healthwatch.py`
- Test: `tests/unit/healthwatch/test_poll.py` (add heartbeat test)

- [ ] **Step 1: Write the failing test for heartbeat write**

Add to `tests/unit/healthwatch/test_poll.py`:

```python
def test_write_heartbeat_is_atomic_and_iso(tmp_path):
    path = tmp_path / "sub" / "heartbeat"
    hw.write_heartbeat(str(path))
    assert path.exists()
    # Parses as an ISO-8601 timestamp.
    from datetime import datetime
    datetime.fromisoformat(path.read_text().strip())
    # No leftover temp file in the directory.
    assert [p.name for p in path.parent.iterdir()] == ["heartbeat"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/healthwatch/test_poll.py::test_write_heartbeat_is_atomic_and_iso -v`
Expected: FAIL — `AttributeError: module 'healthwatch' has no attribute 'write_heartbeat'`.

- [ ] **Step 3: Write minimal implementation**

Add to `docker/healthwatch/healthwatch.py`:

```python
import logging
import os
import socket
import http.client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("healthwatch")


def write_heartbeat(path: str) -> None:
    """Atomically write the current UTC ISO-8601 timestamp to *path*."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(datetime.now(tz=UTC).isoformat())
    os.replace(tmp, path)


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(self._socket_path)
        self.sock = sock


def make_docker_fetch(socket_path: str):
    """Return a ``fetch(path) -> parsed json`` bound to the Docker unix socket."""
    def fetch(path: str):
        conn = _UnixHTTPConnection(socket_path)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"docker GET {path} -> {resp.status}")
            return json.loads(data)
        finally:
            conn.close()
    return fetch


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"missing required env var: {name}")
    return value


def main() -> None:
    socket_path = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    token = _env("DISCORD_BOT_TOKEN")
    channel_id = _env("DISCORD_DEBUG_CHANNEL_ID")
    prefix = os.environ.get("HEALTHWATCH_WATCH_PREFIX", "donna-")
    extras = [
        e.strip()
        for e in os.environ.get("HEALTHWATCH_WATCH_EXTRA", "caddy").split(",")
        if e.strip()
    ]
    interval = int(os.environ.get("HEALTHWATCH_POLL_SECONDS", "30"))
    heartbeat_path = os.environ.get(
        "HEALTHWATCH_HEARTBEAT_PATH", "/var/run/healthwatch/heartbeat"
    )
    host = os.environ.get("HOSTNAME", socket.gethostname())

    fetch = make_docker_fetch(socket_path)
    state: dict[str, str] = {}
    log.info("healthwatch_started prefix=%s extras=%s interval=%ss", prefix, extras, interval)

    while True:
        try:
            cur = poll(fetch, prefix, extras)
        except Exception as exc:  # docker unreachable: skip cycle, do NOT heartbeat
            log.warning("poll_failed: %s", exc)
            time.sleep(interval)
            continue

        for event in diff(state, cur):
            if notify(event, channel_id, token, host):
                state[event.name] = event.status  # advance only on success
                log.info("alerted %s -> %s (%s)", event.name, event.status, event.kind)
            else:
                log.warning("notify_failed %s -> %s; will retry", event.name, event.status)
        # Names with no event keep their prior stored status; refresh OK ones.
        for name, status in cur.items():
            if status == OK:
                state[name] = OK

        write_heartbeat(heartbeat_path)
        time.sleep(interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full watchdog test suite**

Run: `pytest tests/unit/healthwatch/ -v`
Expected: PASS (all tests: classify 7, diff 8, notify 4, poll 5).

- [ ] **Step 5: Commit**

```bash
git add docker/healthwatch/healthwatch.py tests/unit/healthwatch/test_poll.py
git commit -m "feat(healthwatch): docker-socket fetcher, heartbeat write, main loop"
```

---

## Task 6: Watchdog image + compose service

**Files:**
- Create: `docker/Dockerfile.healthwatch`
- Modify: `docker/donna-monitoring.yml`

- [ ] **Step 1: Create the Dockerfile**

Create `docker/Dockerfile.healthwatch`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY docker/healthwatch/healthwatch.py /app/healthwatch.py
RUN useradd -m watcher
USER watcher
ENTRYPOINT ["python", "-u", "/app/healthwatch.py"]
```

- [ ] **Step 2: Add the compose service**

In `docker/donna-monitoring.yml`, add under `services:` (mirror the existing
`restart: unless-stopped` + socket-mount style used by `donna-promtail`):

```yaml
  donna-healthwatch:
    build:
      context: ..
      dockerfile: docker/Dockerfile.healthwatch
    container_name: donna-healthwatch
    restart: unless-stopped
    environment:
      DISCORD_BOT_TOKEN: ${DISCORD_BOT_TOKEN}
      DISCORD_DEBUG_CHANNEL_ID: ${DISCORD_DEBUG_CHANNEL_ID}
      HEALTHWATCH_POLL_SECONDS: ${HEALTHWATCH_POLL_SECONDS:-30}
      HEALTHWATCH_WATCH_PREFIX: ${HEALTHWATCH_WATCH_PREFIX:-donna-}
      HEALTHWATCH_WATCH_EXTRA: ${HEALTHWATCH_WATCH_EXTRA:-caddy}
      HEALTHWATCH_HEARTBEAT_PATH: /var/run/healthwatch/heartbeat
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /mnt/donna/healthwatch:/var/run/healthwatch
    networks:
      - homelab
```

> NOTE: the watcher needs read access to the Docker socket. If the host socket is
> group-owned by `docker` and the `watcher` user lacks that GID, add
> `user: "0:0"` **or** `group_add: ["<docker-gid>"]`. Verify in Step 4; the
> promtail service already reads the socket, so match whatever it relies on.

- [ ] **Step 3: Create the host heartbeat directory**

Run:
```bash
mkdir -p /mnt/donna/healthwatch
```

- [ ] **Step 4: Build, start, and verify it reads the socket + posts**

Run:
```bash
cd /mnt/donna/donna
docker compose -f docker/donna-app.yml -f docker/donna-core.yml -f docker/donna-ui.yml \
  -f docker/donna-monitoring.yml -f docker/donna-ollama.yml --env-file docker/.env \
  up -d --build donna-healthwatch
sleep 5
docker logs --tail 20 donna-healthwatch
ls -l /mnt/donna/healthwatch/heartbeat
```
Expected: log line `healthwatch_started …`, no `poll_failed`, and a heartbeat
file whose contents parse as a recent ISO timestamp. (If `poll_failed: ... 403`
or permission errors appear, apply the socket-GID note from Step 2 and rebuild.)

- [ ] **Step 5: Commit**

```bash
git add docker/Dockerfile.healthwatch docker/donna-monitoring.yml
git commit -m "feat(healthwatch): sidecar image and compose service"
```

---

## Task 7: Orchestrator `is_stale()` + `HeartbeatMonitor`

**Files:**
- Create: `src/donna/healthwatch/__init__.py` (empty)
- Create: `src/donna/healthwatch/heartbeat_monitor.py`
- Test: `tests/unit/healthwatch/test_heartbeat_monitor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/healthwatch/test_heartbeat_monitor.py`:

```python
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from donna.healthwatch.heartbeat_monitor import HeartbeatMonitor, is_stale


def test_is_stale_true_when_old():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(seconds=120)
    assert is_stale(now, old, threshold_seconds=90) is True


def test_is_stale_false_when_fresh():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    recent = now - timedelta(seconds=30)
    assert is_stale(now, recent, threshold_seconds=90) is False


def test_is_stale_true_when_missing():
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    assert is_stale(now, None, threshold_seconds=90) is True


@pytest.mark.asyncio
async def test_monitor_alerts_once_on_stale_then_recovery():
    sent = []

    async def alert(message: str) -> None:
        sent.append(message)

    # heartbeat age returned by injected reader, in seconds: stale, stale, fresh
    ages = iter([200, 200, 5])

    def read_age() -> float | None:
        return next(ages)

    mon = HeartbeatMonitor(alert=alert, read_age_seconds=read_age, threshold_seconds=90)
    await mon.check_once()  # stale -> alert
    await mon.check_once()  # still stale -> no second alert
    await mon.check_once()  # fresh -> recovery alert
    assert len(sent) == 2
    assert "stale" in sent[0].lower()
    assert "resumed" in sent[1].lower() or "recover" in sent[1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/healthwatch/test_heartbeat_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.healthwatch'`.

- [ ] **Step 3: Write minimal implementation**

Create empty `src/donna/healthwatch/__init__.py`. Create
`src/donna/healthwatch/heartbeat_monitor.py`:

```python
"""Reciprocal monitor: alerts when the donna-healthwatch heartbeat goes stale.

This runs *inside* the orchestrator so that the watchdog watching everything
else is itself watched. It reads a heartbeat file written by the sidecar over a
read-only shared volume — deliberately no Docker socket on this side.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


def is_stale(
    now: datetime, last_beat: datetime | None, threshold_seconds: float
) -> bool:
    """True if the heartbeat is missing or older than *threshold_seconds*."""
    if last_beat is None:
        return True
    return (now - last_beat).total_seconds() > threshold_seconds


def _read_age_seconds(path: str) -> float | None:
    """Age of the heartbeat file in seconds, or None if it does not exist."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    return (datetime.now(tz=UTC).timestamp() - mtime)


class HeartbeatMonitor:
    """Edge-triggered watcher of the sidecar heartbeat.

    Args:
        alert: async callback posting a message to the debug channel.
        read_age_seconds: returns heartbeat age in seconds, or None if missing.
        threshold_seconds: age beyond which the heartbeat is considered stale.
        interval_seconds: delay between checks in :meth:`run`.
    """

    def __init__(
        self,
        alert: Callable[[str], Awaitable[None]],
        read_age_seconds: Callable[[], float | None],
        threshold_seconds: float = 90.0,
        interval_seconds: float = 60.0,
    ) -> None:
        self._alert = alert
        self._read_age = read_age_seconds
        self._threshold = threshold_seconds
        self._interval = interval_seconds
        self._stale = False  # last reported state

    async def check_once(self) -> None:
        age = self._read_age()
        stale_now = age is None or age > self._threshold
        if stale_now and not self._stale:
            mins = "unknown" if age is None else f"{age / 60:.1f}m"
            await self._alert(
                f"🟠 **donna-healthwatch** heartbeat stale (last seen {mins} ago) "
                f"— the container watcher may be down"
            )
            self._stale = True
        elif not stale_now and self._stale:
            await self._alert("🟢 **donna-healthwatch** heartbeat resumed")
            self._stale = False

    async def run(self) -> None:
        logger.info("healthwatch_heartbeat_monitor_started", threshold_s=self._threshold)
        while True:
            try:
                await self.check_once()
            except Exception:
                logger.exception("heartbeat_monitor_check_failed")
            await asyncio.sleep(self._interval)


def make_file_reader(path: str) -> Callable[[], float | None]:
    """Bind :func:`_read_age_seconds` to a fixed path."""
    return lambda: _read_age_seconds(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/healthwatch/test_heartbeat_monitor.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/donna/healthwatch/ tests/unit/healthwatch/test_heartbeat_monitor.py
git commit -m "feat(healthwatch): orchestrator heartbeat monitor (reciprocal watch)"
```

---

## Task 8: Wire `HeartbeatMonitor` into the orchestrator + compose

**Files:**
- Modify: `src/donna/server.py` (background-task block, around lines 264-364)
- Modify: `docker/donna-core.yml` (orchestrator service: volume + env)

- [ ] **Step 1: Wire the monitor into server background tasks**

In `src/donna/server.py`, add imports near the top (with the other `donna`
imports):

```python
from donna.healthwatch.heartbeat_monitor import HeartbeatMonitor, make_file_reader
from donna.notifications.service import CHANNEL_DEBUG
```

Then, immediately **after** the `logger.info("notification_background_tasks_started", count=len(bg_tasks))`
line (currently line 364), add:

```python
    # Reciprocal watch: alert if the donna-healthwatch sidecar's heartbeat goes
    # stale. Uses the Discord bot directly (no Docker socket on this service).
    if discord_bot is not None:
        heartbeat_path = os.environ.get(
            "HEALTHWATCH_HEARTBEAT_PATH", "/donna/healthwatch/heartbeat"
        )
        stale_threshold = float(os.environ.get("HEALTHWATCH_STALE_SECONDS", "90"))

        async def _alert_debug(message: str) -> None:
            await discord_bot.send_message(CHANNEL_DEBUG, message)

        heartbeat_monitor = HeartbeatMonitor(
            alert=_alert_debug,
            read_age_seconds=make_file_reader(heartbeat_path),
            threshold_seconds=stale_threshold,
        )
        bg_tasks.append(
            asyncio.create_task(heartbeat_monitor.run(), name="healthwatch_heartbeat_monitor")
        )
        logger.info("healthwatch_heartbeat_monitor_wired", path=heartbeat_path)
```

> Confirm `import os` is already present at the top of `server.py` (it is used
> elsewhere); if not, add it.

- [ ] **Step 2: Add the read-only heartbeat mount + env to the orchestrator**

In `docker/donna-core.yml`, under the `donna-orchestrator` service, add the
shared dir (read-only) to its `volumes:` and the threshold to `environment:`:

```yaml
    volumes:
      - /mnt/donna/healthwatch:/donna/healthwatch:ro
    environment:
      HEALTHWATCH_HEARTBEAT_PATH: /donna/healthwatch/heartbeat
      HEALTHWATCH_STALE_SECONDS: ${HEALTHWATCH_STALE_SECONDS:-90}
```

> These are additions — merge them into the existing `volumes:`/`environment:`
> blocks of `donna-orchestrator`, do not create duplicate keys.

- [ ] **Step 3: Rebuild the orchestrator and verify the monitor wires**

Run:
```bash
cd /mnt/donna/donna
docker compose -f docker/donna-app.yml -f docker/donna-core.yml -f docker/donna-ui.yml \
  -f docker/donna-monitoring.yml -f docker/donna-ollama.yml --env-file docker/.env \
  up -d --build donna-orchestrator
sleep 8
docker logs --tail 40 donna-orchestrator 2>&1 | grep -E "healthwatch_heartbeat_monitor|server_started"
docker inspect --format '{{.State.Health.Status}}' donna-orchestrator
```
Expected: `healthwatch_heartbeat_monitor_wired` and `_started` log lines, and
health reaches `healthy`.

- [ ] **Step 4: End-to-end smoke — stop the watcher, expect a stale alert**

Run:
```bash
docker stop donna-healthwatch
# wait past threshold (90s) + one orchestrator check interval (60s)
sleep 160
docker logs --tail 20 donna-orchestrator 2>&1 | grep -i heartbeat
docker start donna-healthwatch
```
Expected: orchestrator logs show it sent the stale alert; check the Discord
debug channel for `🟠 donna-healthwatch heartbeat stale …` then, after restart,
`🟢 … heartbeat resumed`.

- [ ] **Step 5: Commit**

```bash
git add src/donna/server.py docker/donna-core.yml
git commit -m "feat(healthwatch): wire heartbeat monitor into orchestrator startup"
```

---

## Task 9: Documentation + spec follow-up

**Files:**
- Create: `docs/operations/health-watcher.md`
- Modify: env template (`docker/.env.example` if present)
- Modify: `docs/superpowers/specs/followups.md`

- [ ] **Step 1: Write the operations page**

Create `docs/operations/health-watcher.md`:

```markdown
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
```

- [ ] **Step 2: Document env vars in the template**

If `docker/.env.example` exists, append the five `HEALTHWATCH_*`/Discord rows
above (names + defaults, no secrets). If it does not exist, skip this step.

- [ ] **Step 3: Append a spec follow-up note**

Add to `docs/superpowers/specs/followups.md`:

```markdown
- **2026-06-05 — Container health watcher.** Added `donna-healthwatch` sidecar
  + reciprocal orchestrator heartbeat monitor (observability). Not yet reflected
  in `spec_v3.md`; reconcile the observability section to mention container
  health alerting and the heartbeat contract. Spec ref: this slice's design doc
  `docs/superpowers/specs/2026-06-05-container-health-watcher-design.md`.
```

- [ ] **Step 4: Verify docs build (if the docs toolchain is installed)**

Run: `properdocs build 2>/dev/null || echo "docs toolchain not installed — skip"`
Expected: build succeeds, or the skip message.

- [ ] **Step 5: Commit**

```bash
git add docs/operations/health-watcher.md docs/superpowers/specs/followups.md docker/.env.example 2>/dev/null || git add docs/operations/health-watcher.md docs/superpowers/specs/followups.md
git commit -m "docs(healthwatch): operations page and spec follow-up"
```

---

## Final verification

- [ ] Run the whole watcher test suite: `pytest tests/unit/healthwatch/ -v` → all pass.
- [ ] `docker ps` shows `donna-healthwatch` Up and `donna-orchestrator` healthy.
- [ ] Discord debug channel received the stale/resumed pair from Task 8 Step 4.
- [ ] Optional real-world check: `docker stop donna-api` → expect a `🔴 donna-api … DOWN` message within one poll interval, then `docker start donna-api` → `🟢 donna-api recovered`.

## Self-Review Notes (for the author)

- **Spec coverage:** sidecar (T1-6), edge-trigger+recovery (T2), bot-token Discord (T3), watch set donna-*+caddy (T4), startup-resurfaces-bad (T5 main, via empty initial state), heartbeat+reciprocal monitor (T5/T7/T8), error handling (T5 poll skip / T3 429 / T7 missing-file), tests (T1-7), docs+followups (T9). All covered.
- **Type consistency:** `Event(name, kind, status)` used identically in T2/T3/T5; `classify` record shape `{name,state,health}` identical in T1/T4; `poll(fetch, prefix, extras)` signature identical T4/T5; `HeartbeatMonitor(alert, read_age_seconds, threshold_seconds, interval_seconds)` identical T7/T8.
```
