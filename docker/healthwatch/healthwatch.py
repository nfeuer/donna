#!/usr/bin/env python3
"""donna-healthwatch: standalone container health watcher.

Polls the Docker Engine API over a read-only socket, posts Discord debug-channel
alerts on container health transitions, and writes a heartbeat each cycle.

Stdlib-only and synchronous on purpose: this process must share no dependency or
runtime failure modes with the orchestrator it watches.
"""
from __future__ import annotations

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
