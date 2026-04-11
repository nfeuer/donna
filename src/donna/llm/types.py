"""Types and configuration for the LLM gateway queue system."""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class Priority(enum.IntEnum):
    """Queue priority levels. Lower value = higher priority."""

    CRITICAL = 0
    NORMAL = 1
    BACKGROUND = 2

    @classmethod
    def from_str(cls, value: str) -> Priority:
        try:
            return cls[value.upper()]
        except KeyError:
            return cls.NORMAL


@dataclass
class ChainState:
    """Tracks multi-step inference chains within a single logical request."""

    chain_id: str
    current_step: int = 0
    intermediate_results: list[Any] = field(default_factory=list)


@dataclass
class QueueItem:
    """A single item in the internal or external queue."""

    prompt: str
    model: str
    max_tokens: int
    json_mode: bool
    future: asyncio.Future[Any]
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Internal vs external
    is_internal: bool = False
    priority: Priority = Priority.NORMAL
    task_type: str | None = None
    task_id: str | None = None
    user_id: str = "gateway"
    caller: str | None = None
    # Chain support
    chain: ChainState | None = None
    is_chain_continuation: bool = False
    # Preemption tracking
    interrupted: bool = False
    interrupt_count: int = 0
    # Cloud fallback
    allow_cloud: bool = False

    # Sequence number for FIFO ordering within same priority
    sequence: int = 0

    def __lt__(self, other: QueueItem) -> bool:
        """PriorityQueue comparison: lower priority value first, then FIFO."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.sequence < other.sequence


@dataclass
class GatewayConfig:
    """Parsed gateway configuration with accessors."""

    # Scheduling
    active_hours_start: int = 6
    active_hours_end: int = 22
    schedule_drain_minutes: int = 2
    # Queue
    max_external_depth: int = 20
    max_interrupt_count: int = 3
    # Rate limits
    default_rpm: int = 10
    default_rph: int = 100
    caller_limits: dict[str, dict[str, int]] = field(default_factory=dict)
    # Budget
    daily_external_usd: float = 5.0
    budget_alert_pct: int = 80
    # Cloud
    max_per_request_usd: float = 0.50
    daily_cloud_external_usd: float = 2.0
    # Alerts
    queue_depth_warning: int = 10
    rate_limit_alert_threshold: int = 3
    debounce_minutes: int = 10
    # Priority map
    _priority_map: dict[str, Priority] = field(default_factory=dict)
    # Ollama
    ollama_health_check: bool = True
    # API key
    api_key: str = ""

    def priority_for_task_type(self, task_type: str) -> Priority:
        return self._priority_map.get(task_type, Priority.NORMAL)

    def is_active_hours(self, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        hour = now.hour
        return self.active_hours_start <= hour < self.active_hours_end

    def rpm_for_caller(self, caller: str) -> int:
        limits = self.caller_limits.get(caller, {})
        return limits.get("requests_per_minute", self.default_rpm)

    def rph_for_caller(self, caller: str) -> int:
        limits = self.caller_limits.get(caller, {})
        return limits.get("requests_per_hour", self.default_rph)


def _parse_active_hours(hours_str: str) -> tuple[int, int]:
    """Parse '06:00-22:00' into (6, 22)."""
    parts = hours_str.split("-")
    start = int(parts[0].split(":")[0])
    end = int(parts[1].split(":")[0])
    return start, end


def load_gateway_config(config_dir: Path) -> GatewayConfig:
    """Load gateway config from config/llm_gateway.yaml with defaults."""
    path = config_dir / "llm_gateway.yaml"
    if not path.exists():
        return GatewayConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    sched = raw.get("scheduling", {})
    queue = raw.get("queue", {})
    rl = raw.get("rate_limits", {})
    budget = raw.get("budget", {})
    cloud = raw.get("cloud", {})
    alerts = raw.get("alerts", {})
    pmap_raw = raw.get("priority_map", {})

    hours_str = sched.get("active_hours", "06:00-22:00")
    start, end = _parse_active_hours(hours_str)

    default_rl = rl.get("default", {})
    caller_limits = rl.get("callers", {})

    priority_map = {k: Priority.from_str(v) for k, v in pmap_raw.items()}

    return GatewayConfig(
        active_hours_start=start,
        active_hours_end=end,
        schedule_drain_minutes=int(sched.get("schedule_drain_minutes", 2)),
        max_external_depth=int(queue.get("max_external_depth", 20)),
        max_interrupt_count=int(queue.get("max_interrupt_count", 3)),
        default_rpm=int(default_rl.get("requests_per_minute", 10)),
        default_rph=int(default_rl.get("requests_per_hour", 100)),
        caller_limits=caller_limits,
        daily_external_usd=float(budget.get("daily_external_usd", 5.0)),
        budget_alert_pct=int(budget.get("alert_pct", 80)),
        max_per_request_usd=float(cloud.get("max_per_request_usd", 0.50)),
        daily_cloud_external_usd=float(cloud.get("daily_cloud_external_usd", 2.0)),
        queue_depth_warning=int(alerts.get("queue_depth_warning", 10)),
        rate_limit_alert_threshold=int(alerts.get("rate_limit_alert_threshold", 3)),
        debounce_minutes=int(alerts.get("debounce_minutes", 10)),
        _priority_map=priority_map,
        ollama_health_check=bool(raw.get("ollama_health_check", True)),
        api_key=str(raw.get("api_key", "")),
    )
