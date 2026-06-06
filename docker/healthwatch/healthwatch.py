#!/usr/bin/env python3
"""donna-healthwatch: standalone container health watcher.

Polls the Docker Engine API over a read-only socket, posts Discord debug-channel
alerts on container health transitions, and writes a heartbeat each cycle.

Stdlib-only and synchronous on purpose: this process must share no dependency or
runtime failure modes with the orchestrator it watches.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple, TypedDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("healthwatch")


class ContainerRecord(TypedDict):
    """Normalized container record consumed by :func:`classify`."""
    name: str
    state: str
    health: str | None


# --- Status constants -------------------------------------------------------
OK = "OK"
UNHEALTHY = "UNHEALTHY"
DOWN = "DOWN"
MISSING = "MISSING"

_RUNNING_BAD_HEALTH = {"unhealthy"}


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
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def notify(
    event: Event,
    channel_id: str,
    token: str,
    host: str,
    poster: Callable[[str, dict[str, str], str], tuple[int, str]] = _http_post,
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
        except (ValueError, TypeError, AttributeError):
            pass
        time.sleep(min(retry_after, 30.0))
        status, text = poster(url, headers, body)
    return 200 <= status < 300


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


def poll(fetch: Callable[[str], list[dict]], prefix: str, extras: list[str]) -> dict[str, str]:
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


def classify(record: ContainerRecord) -> str:
    """Map a normalized container record to a status constant.

    Args:
        record: ``{"name": str, "state": str, "health": str | None}`` where
            ``state`` is a Docker state (running/exited/...) and ``health`` is
            ``healthy``/``unhealthy``/``starting``/``None``.

    Returns:
        One of ``OK``, ``UNHEALTHY``, ``DOWN``. Never returns ``MISSING`` —
        that status is set by ``poll()`` for containers absent from the
        Docker list.
    """
    state = record.get("state")
    if state == "running":
        if record.get("health") in _RUNNING_BAD_HEALTH:
            return UNHEALTHY
        return OK
    return DOWN
