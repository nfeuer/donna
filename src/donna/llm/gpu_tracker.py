"""GpuTracker — tracks GPU model state, swap metrics, and alert thresholds."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import structlog

from donna.llm.types import GpuConfig

logger = structlog.get_logger()


@dataclass
class SwapRecord:
    """Record of a single model swap."""

    from_model: str | None
    to_model: str
    started_at: float
    duration_ms: int = 0
    completed: bool = False


@dataclass
class ExecRecord:
    """Timestamped execution duration for rolling overhead calculation."""

    timestamp: float
    duration_ms: int


class GpuTracker:
    """Tracks the currently loaded GPU model and rolling swap metrics.

    Not thread-safe — designed for single-worker access within LLMQueueWorker.
    """

    def __init__(self, config: GpuConfig) -> None:
        self._config = config
        self._loaded_model: str | None = None
        self._swaps: deque[SwapRecord] = deque(maxlen=200)
        self._execs: deque[ExecRecord] = deque(maxlen=500)
        self._current_swap: SwapRecord | None = None

    @property
    def loaded_model(self) -> str | None:
        return self._loaded_model

    @property
    def is_home(self) -> bool:
        return self._loaded_model == self._config.home_model

    @property
    def home_model(self) -> str:
        return self._config.home_model

    @property
    def swaps_this_hour(self) -> int:
        cutoff = time.monotonic() - 3600
        return sum(1 for s in self._swaps if s.completed and s.started_at > cutoff)

    def record_loaded(self, model: str) -> None:
        self._loaded_model = model

    def record_swap_started(self, to_model: str) -> None:
        self._current_swap = SwapRecord(
            from_model=self._loaded_model,
            to_model=to_model,
            started_at=time.monotonic(),
        )
        logger.info(
            "gpu_swap_started",
            from_model=self._loaded_model,
            to_model=to_model,
        )

    def record_swap_completed(self, to_model: str, duration_ms: int) -> None:
        if self._current_swap is not None:
            self._current_swap.duration_ms = duration_ms
            self._current_swap.completed = True
            self._swaps.append(self._current_swap)
            self._current_swap = None

        self._loaded_model = to_model

        logger.info(
            "gpu_swap_completed",
            to_model=to_model,
            duration_ms=duration_ms,
            swaps_this_hour=self.swaps_this_hour,
        )

    def record_execution_time(self, duration_ms: int) -> None:
        self._execs.append(ExecRecord(
            timestamp=time.monotonic(),
            duration_ms=duration_ms,
        ))

    def update_config(self, config: GpuConfig) -> None:
        self._config = config

    def get_metrics(self) -> dict[str, Any]:
        cutoff = time.monotonic() - 3600
        recent_swaps = [s for s in self._swaps if s.completed and s.started_at > cutoff]
        swap_count = len(recent_swaps)

        avg_swap_ms = 0
        if recent_swaps:
            avg_swap_ms = sum(s.duration_ms for s in recent_swaps) // len(recent_swaps)

        last_swap_ms = recent_swaps[-1].duration_ms if recent_swaps else 0

        swap_ms_1h = sum(s.duration_ms for s in recent_swaps)
        exec_ms_1h = sum(e.duration_ms for e in self._execs if e.timestamp > cutoff)
        total_time = swap_ms_1h + exec_ms_1h
        overhead_pct = (
            round(swap_ms_1h / total_time * 100, 1) if total_time > 0 else 0.0
        )

        return {
            "loaded_model": self._loaded_model,
            "is_home": self.is_home,
            "swaps_this_hour": swap_count,
            "last_swap_duration_ms": last_swap_ms,
            "avg_swap_duration_ms_1h": avg_swap_ms,
            "swap_overhead_pct_1h": overhead_pct,
        }

    def check_alerts(self) -> list[str]:
        alerts: list[str] = []
        metrics = self.get_metrics()

        if metrics["swaps_this_hour"] > self._config.swaps_per_hour_warning:
            alerts.append(
                f"GPU swapped {metrics['swaps_this_hour']} times in the last hour. "
                "Consider consolidating automation schedules."
            )

        if metrics["last_swap_duration_ms"] > self._config.swap_wait_ms_warning:
            secs = metrics["last_swap_duration_ms"] / 1000
            alerts.append(
                f"Last GPU swap took {secs:.0f}s. Model loading is slow — check Ollama health."
            )

        if metrics["swap_overhead_pct_1h"] > self._config.swap_overhead_pct_warning:
            alerts.append(
                f"{metrics['swap_overhead_pct_1h']}% of queue time spent loading models. "
                "Review model affinity groupings."
            )

        return alerts
