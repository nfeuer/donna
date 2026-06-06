#!/usr/bin/env python3
"""donna-healthwatch: standalone container health watcher.

Polls the Docker Engine API over a read-only socket, posts Discord debug-channel
alerts on container health transitions, and writes a heartbeat each cycle.

Stdlib-only and synchronous on purpose: this process must share no dependency or
runtime failure modes with the orchestrator it watches.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NamedTuple, TypedDict


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
