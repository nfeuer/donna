#!/usr/bin/env python3
"""donna-healthwatch: standalone container health watcher.

Polls the Docker Engine API over a read-only socket, posts Discord debug-channel
alerts on container health transitions, and writes a heartbeat each cycle.

Stdlib-only and synchronous on purpose: this process must share no dependency or
runtime failure modes with the orchestrator it watches.
"""
from __future__ import annotations

from typing import TypedDict


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
