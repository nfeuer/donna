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
    """Return True if the heartbeat is missing or older than *threshold_seconds*.

    Args:
        now: Current time (timezone-aware).
        last_beat: Timestamp of the last heartbeat, or None if unavailable.
        threshold_seconds: Age beyond which the heartbeat is considered stale.

    Returns:
        True if missing or older than the threshold, else False.
    """
    if last_beat is None:
        return True
    return (now - last_beat).total_seconds() > threshold_seconds


def _read_age_seconds(path: str) -> float | None:
    """Return the age of the heartbeat file in seconds, or None if absent.

    Args:
        path: Filesystem path to the heartbeat file.

    Returns:
        Seconds since the file was last modified, or None if it does not exist.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    return datetime.now(tz=UTC).timestamp() - mtime


class HeartbeatMonitor:
    """Edge-triggered watcher of the donna-healthwatch sidecar heartbeat.

    Alerts once when the heartbeat goes stale and once when it recovers, so a
    dead or hung watcher is surfaced without repeating the alert every cycle.

    Args:
        alert: Async callback posting a message to the debug channel.
        read_age_seconds: Returns heartbeat age in seconds, or None if missing.
        threshold_seconds: Age beyond which the heartbeat is considered stale.
        interval_seconds: Delay between checks in :meth:`run`.
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
        """Check the heartbeat once and emit an edge-triggered alert if needed."""
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
        """Run the check loop forever, sleeping *interval_seconds* between checks."""
        logger.info("healthwatch_heartbeat_monitor_started", threshold_s=self._threshold)
        while True:
            try:
                await self.check_once()
            except Exception:
                logger.exception("heartbeat_monitor_check_failed")
            await asyncio.sleep(self._interval)


def make_file_reader(path: str) -> Callable[[], float | None]:
    """Return a zero-arg reader bound to *path* for :class:`HeartbeatMonitor`.

    Args:
        path: Filesystem path to the heartbeat file.

    Returns:
        A callable returning the heartbeat age in seconds, or None if absent.
    """
    return lambda: _read_age_seconds(path)
