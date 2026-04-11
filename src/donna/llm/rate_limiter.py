"""Per-caller sliding window rate limiter for the LLM gateway."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _CallerState:
    """Sliding window state for one caller."""

    minute_timestamps: list[float] = field(default_factory=list)
    hour_timestamps: list[float] = field(default_factory=list)
    rejection_timestamps: list[float] = field(default_factory=list)


class RateLimiter:
    """Per-caller sliding window rate limiter.

    Tracks request timestamps per caller and rejects when limits
    are exceeded. Counters survive hot-reload via update_limits().
    On startup, call rebuild_from_records() per caller to restore
    approximate state from invocation_log.
    """

    def __init__(
        self,
        default_rpm: int,
        default_rph: int,
        caller_limits: dict[str, dict[str, int]],
    ) -> None:
        self._default_rpm = default_rpm
        self._default_rph = default_rph
        self._caller_limits = caller_limits
        self._state: dict[str, _CallerState] = defaultdict(_CallerState)

    def check(self, caller: str) -> bool:
        """Check if caller is within rate limits. Returns True if allowed."""
        now = time.monotonic()
        state = self._state[caller]

        # Prune old entries
        minute_cutoff = now - 60
        hour_cutoff = now - 3600
        state.minute_timestamps = [t for t in state.minute_timestamps if t > minute_cutoff]
        state.hour_timestamps = [t for t in state.hour_timestamps if t > hour_cutoff]

        rpm = self._caller_limits.get(caller, {}).get(
            "requests_per_minute", self._default_rpm
        )
        rph = self._caller_limits.get(caller, {}).get(
            "requests_per_hour", self._default_rph
        )

        if len(state.minute_timestamps) >= rpm or len(state.hour_timestamps) >= rph:
            state.rejection_timestamps.append(now)
            return False

        state.minute_timestamps.append(now)
        state.hour_timestamps.append(now)
        return True

    def recent_rejections(self, caller: str, window_seconds: int = 300) -> int:
        """Count rejections for a caller in the last N seconds."""
        now = time.monotonic()
        cutoff = now - window_seconds
        state = self._state.get(caller)
        if state is None:
            return 0
        return sum(1 for t in state.rejection_timestamps if t > cutoff)

    def get_usage(self, caller: str) -> dict[str, int]:
        """Return current usage counters for a caller."""
        now = time.monotonic()
        state = self._state.get(caller, _CallerState())

        minute_cutoff = now - 60
        hour_cutoff = now - 3600
        minute_count = sum(1 for t in state.minute_timestamps if t > minute_cutoff)
        hour_count = sum(1 for t in state.hour_timestamps if t > hour_cutoff)

        rpm = self._caller_limits.get(caller, {}).get(
            "requests_per_minute", self._default_rpm
        )
        rph = self._caller_limits.get(caller, {}).get(
            "requests_per_hour", self._default_rph
        )

        return {
            "minute_count": minute_count,
            "minute_limit": rpm,
            "hour_count": hour_count,
            "hour_limit": rph,
        }

    def get_all_usage(self) -> dict[str, dict[str, str]]:
        """Return formatted usage for all known callers (for status endpoint)."""
        result = {}
        for caller in self._state:
            usage = self.get_usage(caller)
            result[caller] = {
                "minute": f"{usage['minute_count']}/{usage['minute_limit']}",
                "hour": f"{usage['hour_count']}/{usage['hour_limit']}",
            }
        return result

    def rebuild_from_records(self, caller: str, call_count_last_hour: int) -> None:
        """Rebuild approximate state from invocation_log on startup.

        Creates synthetic timestamps spread across the last hour.
        """
        now = time.monotonic()
        state = self._state[caller]
        # Spread timestamps evenly across the last hour
        if call_count_last_hour > 0:
            # Spread timestamps evenly, offset slightly inside the window boundary
            interval = 3599 / max(call_count_last_hour, 1)
            for i in range(call_count_last_hour):
                ts = now - 3599 + (i * interval)
                state.hour_timestamps.append(ts)
                # Only the most recent ones count for the minute window
                if ts > now - 60:
                    state.minute_timestamps.append(ts)

    def update_limits(
        self,
        default_rpm: int,
        default_rph: int,
        caller_limits: dict[str, dict[str, int]],
    ) -> None:
        """Hot-reload: update limits without clearing counters."""
        self._default_rpm = default_rpm
        self._default_rph = default_rph
        self._caller_limits = caller_limits
