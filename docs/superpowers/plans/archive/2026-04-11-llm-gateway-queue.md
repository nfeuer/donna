# LLM Gateway Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a priority queue system to the LLM gateway so Donna's internal tasks always take priority over external API calls, with preemption, rate limiting, budget separation, alerting, and observability.

**Architecture:** Two asyncio queues (internal PriorityQueue + external FIFO Queue) served by a single worker coroutine. The worker pops from the internal queue first, preempts running external requests during active hours, and drains before scheduled tasks. Rate limiting, budget checks, and alerting happen at the gateway boundary before enqueuing.

**Tech Stack:** Python 3.12 asyncio, FastAPI, aiohttp, structlog, aiosqlite, Alembic, pytest

**Spec:** `docs/superpowers/specs/2026-04-11-llm-gateway-queue-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/donna/llm/__init__.py` | Package init |
| `src/donna/llm/types.py` | `QueueItem`, `ChainState`, `Priority` enum, `GatewayConfig` dataclass |
| `src/donna/llm/rate_limiter.py` | `RateLimiter` — per-caller sliding window counters |
| `src/donna/llm/queue.py` | `LLMQueueWorker` — two queues, popper logic, worker loop, preemption |
| `src/donna/llm/alerter.py` | `GatewayAlerter` — debounced Discord alerts for gateway events |
| `config/llm_gateway.yaml` | Gateway configuration (new file, split from donna_models.yaml) |
| `alembic/versions/add_llm_gateway_columns.py` | Migration: add queue_wait_ms, interrupted, chain_id, caller to invocation_log |
| `tests/unit/test_llm_types.py` | Tests for types and config loading |
| `tests/unit/test_llm_rate_limiter.py` | Tests for rate limiter |
| `tests/unit/test_llm_queue.py` | Tests for queue ordering, preemption, chain handling |
| `tests/unit/test_llm_alerter.py` | Tests for alerter debouncing |

**Modified files:**

| File | Change |
|------|--------|
| `src/donna/models/providers/ollama.py` | Add `json_mode` parameter to `complete()` |
| `src/donna/logging/invocation_logger.py` | Add `queue_wait_ms`, `interrupted`, `chain_id`, `caller` fields to `InvocationMetadata` and INSERT |
| `src/donna/cost/tracker.py` | Add `exclude_task_types` parameter to `_sum_range()` and `get_daily_cost()` |
| `src/donna/cost/budget.py` | Exclude `external_llm_call` from `check_pre_call()` |
| `src/donna/models/router.py` | Add optional `llm_queue` parameter, enqueue Ollama calls through it |
| `src/donna/api/routes/llm.py` | Rewrite — enqueue via worker, remove `/chat`, add `/queue/status`, add `allow_cloud` and `json_mode` |
| `src/donna/api/routes/admin_config.py` | Add `llm_gateway.yaml` to allowed configs, add post-save hot-reload hook |
| `src/donna/api/routes/admin_health.py` | Use config flag for Ollama health check |
| `src/donna/api/routes/admin_logs.py` | Update `llm_gateway` event types |
| `src/donna/api/__init__.py` | Lifespan creates `LLMQueueWorker`, loads gateway config, middleware excludes health paths |
| `config/donna_models.yaml` | Remove `llm_gateway` section |
| `scripts/donna-up.sh` | Add `--env-file` |
| `scripts/donna-down.sh` | Add `--env-file` |

---

### Task 1: Gateway Config and Types

**Files:**
- Create: `src/donna/llm/__init__.py`
- Create: `src/donna/llm/types.py`
- Create: `config/llm_gateway.yaml`
- Create: `tests/unit/test_llm_types.py`

- [ ] **Step 1: Write the failing test for config loading and types**

```python
# tests/unit/test_llm_types.py
"""Tests for LLM gateway types and config loading."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from donna.llm.types import GatewayConfig, Priority, load_gateway_config


class TestPriority:
    def test_ordering(self) -> None:
        assert Priority.CRITICAL < Priority.NORMAL < Priority.BACKGROUND

    def test_from_str(self) -> None:
        assert Priority.from_str("critical") == Priority.CRITICAL
        assert Priority.from_str("normal") == Priority.NORMAL
        assert Priority.from_str("background") == Priority.BACKGROUND

    def test_from_str_unknown_defaults_normal(self) -> None:
        assert Priority.from_str("unknown") == Priority.NORMAL


class TestGatewayConfig:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "llm_gateway.yaml"
        config_file.write_text(textwrap.dedent("""\
            scheduling:
              active_hours: "07:00-21:00"
              schedule_drain_minutes: 3
            queue:
              max_external_depth: 15
              max_interrupt_count: 5
            rate_limits:
              default:
                requests_per_minute: 5
                requests_per_hour: 50
            budget:
              daily_external_usd: 10.0
              alert_pct: 90
            cloud:
              max_per_request_usd: 1.0
              daily_cloud_external_usd: 5.0
            alerts:
              queue_depth_warning: 8
              rate_limit_alert_threshold: 2
              debounce_minutes: 5
            priority_map:
              parse_task: critical
              generate_nudge: background
            ollama_health_check: false
        """))
        cfg = load_gateway_config(tmp_path)

        assert cfg.active_hours_start == 7
        assert cfg.active_hours_end == 21
        assert cfg.schedule_drain_minutes == 3
        assert cfg.max_external_depth == 15
        assert cfg.max_interrupt_count == 5
        assert cfg.default_rpm == 5
        assert cfg.default_rph == 50
        assert cfg.daily_external_usd == 10.0
        assert cfg.max_per_request_usd == 1.0
        assert cfg.priority_for_task_type("parse_task") == Priority.CRITICAL
        assert cfg.priority_for_task_type("generate_nudge") == Priority.BACKGROUND
        assert cfg.priority_for_task_type("unknown_task") == Priority.NORMAL
        assert cfg.ollama_health_check is False

    def test_load_defaults_when_file_missing(self, tmp_path: Path) -> None:
        cfg = load_gateway_config(tmp_path)
        assert cfg.active_hours_start == 6
        assert cfg.active_hours_end == 22
        assert cfg.max_external_depth == 20
        assert cfg.default_rpm == 10
        assert cfg.daily_external_usd == 5.0
        assert cfg.ollama_health_check is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_llm_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'donna.llm'`

- [ ] **Step 3: Create the package init**

```python
# src/donna/llm/__init__.py
```

Empty file — just makes it a package.

- [ ] **Step 4: Write the types module**

```python
# src/donna/llm/types.py
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
```

- [ ] **Step 5: Create the gateway config file**

```yaml
# config/llm_gateway.yaml
# LLM Gateway Configuration — editable via dashboard, hot-reloaded on save.

scheduling:
  active_hours: "06:00-22:00"
  schedule_drain_minutes: 2

queue:
  max_external_depth: 20
  max_interrupt_count: 3

priority_map:
  parse_task: critical
  challenge_task: critical
  generate_digest: normal
  extract_preferences: normal
  dedup_check: normal
  prep_research: normal
  task_decompose: normal
  generate_nudge: background
  generate_reminder: background
  generate_weekly_digest: normal

rate_limits:
  default:
    requests_per_minute: 10
    requests_per_hour: 100
  callers: {}

budget:
  daily_external_usd: 5.00
  alert_pct: 80

cloud:
  max_per_request_usd: 0.50
  daily_cloud_external_usd: 2.00

alerts:
  queue_depth_warning: 10
  rate_limit_alert_threshold: 3
  debounce_minutes: 10

ollama_health_check: true
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_llm_types.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/donna/llm/__init__.py src/donna/llm/types.py config/llm_gateway.yaml tests/unit/test_llm_types.py
git commit -m "feat(llm-gateway): add queue types, priority enum, and gateway config loader"
```

---

### Task 2: Rate Limiter

**Files:**
- Create: `src/donna/llm/rate_limiter.py`
- Create: `tests/unit/test_llm_rate_limiter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm_rate_limiter.py
"""Tests for per-caller rate limiting."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from donna.llm.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_under_limit(self) -> None:
        rl = RateLimiter(default_rpm=5, default_rph=100, caller_limits={})
        for _ in range(5):
            assert rl.check("test-caller") is True

    def test_rejects_over_minute_limit(self) -> None:
        rl = RateLimiter(default_rpm=2, default_rph=100, caller_limits={})
        assert rl.check("test-caller") is True
        assert rl.check("test-caller") is True
        assert rl.check("test-caller") is False

    def test_per_caller_limits_override_default(self) -> None:
        rl = RateLimiter(
            default_rpm=2,
            default_rph=100,
            caller_limits={"fast-caller": {"requests_per_minute": 10}},
        )
        for _ in range(10):
            assert rl.check("fast-caller") is True
        # But default still limited
        rl.check("slow-caller")
        rl.check("slow-caller")
        assert rl.check("slow-caller") is False

    def test_separate_callers_have_separate_counters(self) -> None:
        rl = RateLimiter(default_rpm=1, default_rph=100, caller_limits={})
        assert rl.check("caller-a") is True
        assert rl.check("caller-a") is False
        assert rl.check("caller-b") is True  # separate counter

    def test_rejection_count_tracking(self) -> None:
        rl = RateLimiter(default_rpm=1, default_rph=100, caller_limits={})
        rl.check("test-caller")
        rl.check("test-caller")  # rejected
        rl.check("test-caller")  # rejected
        assert rl.recent_rejections("test-caller", window_seconds=300) == 2

    def test_get_usage_for_caller(self) -> None:
        rl = RateLimiter(default_rpm=10, default_rph=100, caller_limits={})
        rl.check("test-caller")
        rl.check("test-caller")
        usage = rl.get_usage("test-caller")
        assert usage["minute_count"] == 2
        assert usage["minute_limit"] == 10

    def test_rebuild_from_records(self) -> None:
        """Simulate rebuilding counters from invocation_log on startup."""
        rl = RateLimiter(default_rpm=10, default_rph=5, caller_limits={})
        now = time.monotonic()
        # Simulate 5 recent calls from a caller
        rl.rebuild_from_records("busy-caller", call_count_last_hour=5)
        assert rl.check("busy-caller") is False  # at hour limit

    def test_update_limits(self) -> None:
        """Hot-reload: update limits without losing counters."""
        rl = RateLimiter(default_rpm=5, default_rph=100, caller_limits={})
        rl.check("test-caller")
        rl.check("test-caller")
        rl.update_limits(default_rpm=10, default_rph=200, caller_limits={})
        # Counters preserved, but new limits apply
        assert rl.check("test-caller") is True  # still under new limit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_llm_rate_limiter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the rate limiter**

```python
# src/donna/llm/rate_limiter.py
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
            interval = 3600 / call_count_last_hour
            for i in range(call_count_last_hour):
                ts = now - 3600 + (i * interval)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_llm_rate_limiter.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/llm/rate_limiter.py tests/unit/test_llm_rate_limiter.py
git commit -m "feat(llm-gateway): add per-caller sliding window rate limiter"
```

---

### Task 3: Gateway Alerter

**Files:**
- Create: `src/donna/llm/alerter.py`
- Create: `tests/unit/test_llm_alerter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm_alerter.py
"""Tests for gateway alerter with debouncing."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from donna.llm.alerter import GatewayAlerter


class TestGatewayAlerter:
    async def test_sends_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        notifier.assert_called_once()
        call_args = notifier.call_args
        assert "test-caller" in call_args[0][1]

    async def test_debounces_same_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        assert notifier.call_count == 1  # debounced

    async def test_different_callers_not_debounced(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("caller-a", current_rpm=15, limit_rpm=10)
        await alerter.alert_rate_limited("caller-b", current_rpm=15, limit_rpm=10)
        assert notifier.call_count == 2

    async def test_different_alert_types_not_debounced(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_rate_limited("test-caller", current_rpm=15, limit_rpm=10)
        await alerter.alert_queue_depth(current_depth=15, warning_threshold=10)
        assert notifier.call_count == 2

    async def test_queue_depth_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_queue_depth(current_depth=15, warning_threshold=10)
        assert "15" in notifier.call_args[0][1]

    async def test_starvation_alert(self) -> None:
        notifier = AsyncMock()
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        await alerter.alert_starvation("test-caller", interrupt_count=3)
        assert "test-caller" in notifier.call_args[0][1]
        assert "3" in notifier.call_args[0][1]

    async def test_notifier_failure_does_not_raise(self) -> None:
        notifier = AsyncMock(side_effect=Exception("Discord down"))
        alerter = GatewayAlerter(notifier=notifier, debounce_minutes=10)
        # Should not raise
        await alerter.alert_queue_depth(current_depth=15, warning_threshold=10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_llm_alerter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the alerter**

```python
# src/donna/llm/alerter.py
"""Debounced Discord alerting for LLM gateway events."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger()

# Notifier type matches BudgetGuard: async callable(channel_name, message)
Notifier = Callable[[str, str], Awaitable[None]]


class GatewayAlerter:
    """Sends debounced alerts to Discord for gateway events.

    Each (alert_type, caller) pair is debounced independently.
    """

    def __init__(
        self,
        notifier: Notifier,
        debounce_minutes: int = 10,
    ) -> None:
        self._notifier = notifier
        self._debounce_seconds = debounce_minutes * 60
        self._last_sent: dict[str, float] = {}

    async def _send(self, key: str, message: str) -> None:
        """Send alert if not debounced."""
        now = time.monotonic()
        last = self._last_sent.get(key, 0)
        if now - last < self._debounce_seconds:
            return
        self._last_sent[key] = now
        try:
            await self._notifier("debug", message)
        except Exception:
            logger.exception("gateway_alert_failed", key=key)

    async def alert_rate_limited(
        self, caller: str, current_rpm: int, limit_rpm: int
    ) -> None:
        key = f"rate_limit:{caller}"
        msg = (
            f"LLM Gateway: **{caller}** is being rate-limited — "
            f"{current_rpm} req/min (limit: {limit_rpm})"
        )
        await self._send(key, msg)

    async def alert_queue_depth(
        self, current_depth: int, warning_threshold: int
    ) -> None:
        key = "queue_depth"
        msg = (
            f"LLM Gateway: backlog — "
            f"{current_depth} external requests queued "
            f"(warning at {warning_threshold})"
        )
        await self._send(key, msg)

    async def alert_queue_full(self, caller: str, max_depth: int) -> None:
        key = f"queue_full:{caller}"
        msg = (
            f"LLM Gateway: full — rejecting requests from **{caller}** "
            f"(queue: {max_depth}/{max_depth})"
        )
        await self._send(key, msg)

    async def alert_starvation(self, caller: str, interrupt_count: int) -> None:
        key = f"starvation:{caller}"
        msg = (
            f"LLM Gateway: external request from **{caller}** interrupted "
            f"{interrupt_count}x — promoting to prevent starvation"
        )
        await self._send(key, msg)

    async def alert_budget(
        self, spent: float, limit: float, pct: int
    ) -> None:
        key = "external_budget"
        msg = (
            f"LLM Gateway: external spend at {pct}% of daily limit "
            f"(${spent:.2f}/${limit:.2f})"
        )
        await self._send(key, msg)

    def update_debounce(self, debounce_minutes: int) -> None:
        """Hot-reload debounce interval."""
        self._debounce_seconds = debounce_minutes * 60
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_llm_alerter.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/llm/alerter.py tests/unit/test_llm_alerter.py
git commit -m "feat(llm-gateway): add debounced gateway alerter"
```

---

### Task 4: Database Migration and InvocationLogger Updates

**Files:**
- Create: `alembic/versions/add_llm_gateway_columns.py`
- Modify: `src/donna/logging/invocation_logger.py:22-39` (InvocationMetadata fields)
- Modify: `src/donna/logging/invocation_logger.py:53-79` (INSERT statement)
- Test: `tests/unit/test_llm_gateway.py` (update existing tests)

- [ ] **Step 1: Write the failing test for new InvocationMetadata fields**

```python
# Add to tests/unit/test_llm_gateway.py — replace the _make_metadata helper and add:

def test_invocation_metadata_has_gateway_fields() -> None:
    from donna.logging.invocation_logger import InvocationMetadata

    meta = InvocationMetadata(
        task_type="external_llm_call",
        model_alias="gateway/test",
        model_actual="ollama/qwen",
        input_hash="abc123",
        latency_ms=500,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        user_id="gateway",
        queue_wait_ms=1200,
        interrupted=True,
        chain_id="chain-xyz",
        caller="immich-tagger",
    )
    assert meta.queue_wait_ms == 1200
    assert meta.interrupted is True
    assert meta.chain_id == "chain-xyz"
    assert meta.caller == "immich-tagger"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_llm_gateway.py::test_invocation_metadata_has_gateway_fields -v`
Expected: FAIL with `TypeError: unexpected keyword argument`

- [ ] **Step 3: Add fields to InvocationMetadata**

In `src/donna/logging/invocation_logger.py`, add after line 39 (`spot_check_queued: bool = False`):

```python
    queue_wait_ms: int | None = None
    interrupted: bool = False
    chain_id: str | None = None
    caller: str | None = None
```

- [ ] **Step 4: Update the INSERT statement**

In `src/donna/logging/invocation_logger.py`, replace the INSERT block (lines 53-79) with:

```python
        await self._conn.execute(
            """INSERT INTO invocation_log
            (id, timestamp, task_type, task_id, model_alias, model_actual,
             input_hash, latency_ms, tokens_in, tokens_out, cost_usd,
             output, quality_score, is_shadow, eval_session_id,
             spot_check_queued, user_id,
             queue_wait_ms, interrupted, chain_id, caller)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invocation_id,
                now,
                metadata.task_type,
                metadata.task_id,
                metadata.model_alias,
                metadata.model_actual,
                metadata.input_hash,
                metadata.latency_ms,
                metadata.tokens_in,
                metadata.tokens_out,
                metadata.cost_usd,
                json.dumps(metadata.output) if metadata.output is not None else None,
                metadata.quality_score,
                metadata.is_shadow,
                metadata.eval_session_id,
                metadata.spot_check_queued,
                metadata.user_id,
                metadata.queue_wait_ms,
                metadata.interrupted,
                metadata.chain_id,
                metadata.caller,
            ),
        )
```

- [ ] **Step 5: Create the Alembic migration**

```python
# alembic/versions/add_llm_gateway_columns.py
"""add LLM gateway columns to invocation_log

Revision ID: e7a3b4c5d692
Revises: d5f9a7c3e281
Create Date: 2026-04-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3b4c5d692"
down_revision: Union[str, None] = "d5f9a7c3e281"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("queue_wait_ms", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("interrupted", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("chain_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("caller", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_column("caller")
        batch_op.drop_column("chain_id")
        batch_op.drop_column("interrupted")
        batch_op.drop_column("queue_wait_ms")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_llm_gateway.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/add_llm_gateway_columns.py src/donna/logging/invocation_logger.py tests/unit/test_llm_gateway.py
git commit -m "feat(llm-gateway): add invocation_log columns for queue tracking"
```

---

### Task 5: CostTracker and BudgetGuard Separation

**Files:**
- Modify: `src/donna/cost/tracker.py:42-56` (get_daily_cost)
- Modify: `src/donna/cost/tracker.py:143-154` (_sum_range)
- Modify: `src/donna/cost/budget.py:73` (check_pre_call)
- Test: `tests/unit/test_cost_tracker.py` (new)
- Test: `tests/unit/test_budget_guard.py` (new)

- [ ] **Step 1: Write the failing test for exclude_task_types**

```python
# tests/unit/test_cost_separation.py
"""Tests for budget separation between internal and external LLM calls."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.cost.tracker import CostTracker


class TestCostTrackerExclusion:
    async def test_get_daily_cost_excludes_task_types(self) -> None:
        conn = AsyncMock()

        # Mock: total including external = $25, without external = $15
        cursor_total = AsyncMock()
        cursor_total.fetchone = AsyncMock(return_value=(15.0, 50))
        cursor_breakdown = AsyncMock()
        cursor_breakdown.fetchall = AsyncMock(return_value=[
            ("parse_task", 10.0),
            ("generate_digest", 5.0),
        ])
        conn.execute = AsyncMock(side_effect=[cursor_total, cursor_breakdown])

        tracker = CostTracker(conn)
        result = await tracker.get_daily_cost(
            exclude_task_types=["external_llm_call"]
        )

        # Verify the SQL included the exclusion
        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "task_type NOT IN" in first_call_sql
        assert result.total_usd == 15.0

    async def test_get_daily_cost_no_exclusion_by_default(self) -> None:
        conn = AsyncMock()
        cursor_total = AsyncMock()
        cursor_total.fetchone = AsyncMock(return_value=(25.0, 80))
        cursor_breakdown = AsyncMock()
        cursor_breakdown.fetchall = AsyncMock(return_value=[])
        conn.execute = AsyncMock(side_effect=[cursor_total, cursor_breakdown])

        tracker = CostTracker(conn)
        result = await tracker.get_daily_cost()

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "NOT IN" not in first_call_sql
        assert result.total_usd == 25.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_cost_separation.py -v`
Expected: FAIL with `TypeError: got an unexpected keyword argument 'exclude_task_types'`

- [ ] **Step 3: Add exclude_task_types to CostTracker**

In `src/donna/cost/tracker.py`, modify `_sum_range` (lines 143-154):

```python
    async def _sum_range(
        self,
        start: str,
        end: str,
        exclude_task_types: list[str] | None = None,
    ) -> tuple[float, int]:
        """Return (total_cost, call_count) for a timestamp range."""
        where = "timestamp >= ? AND timestamp <= ?"
        params: list[Any] = [start, end]
        if exclude_task_types:
            placeholders = ", ".join("?" for _ in exclude_task_types)
            where += f" AND task_type NOT IN ({placeholders})"
            params.extend(exclude_task_types)
        cursor = await self._conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0), COUNT(*) FROM invocation_log WHERE {where}",
            params,
        )
        row = await cursor.fetchone()
        if row is None:
            return 0.0, 0
        return float(row[0]), int(row[1])
```

Add `from typing import Any` to the imports at the top of the file.

Also modify `_breakdown_by_task_type` similarly:

```python
    async def _breakdown_by_task_type(
        self,
        start: str,
        end: str,
        exclude_task_types: list[str] | None = None,
    ) -> dict[str, float]:
        """Cost grouped by task_type for a timestamp range."""
        where = "timestamp >= ? AND timestamp <= ?"
        params: list[Any] = [start, end]
        if exclude_task_types:
            placeholders = ", ".join("?" for _ in exclude_task_types)
            where += f" AND task_type NOT IN ({placeholders})"
            params.extend(exclude_task_types)
        cursor = await self._conn.execute(
            f"SELECT task_type, SUM(cost_usd) FROM invocation_log WHERE {where} GROUP BY task_type",
            params,
        )
        rows = await cursor.fetchall()
        return {row[0]: float(row[1]) for row in rows}
```

Then modify `get_daily_cost` (line 42) to accept and pass through the parameter:

```python
    async def get_daily_cost(
        self,
        for_date: date | None = None,
        exclude_task_types: list[str] | None = None,
    ) -> CostSummary:
```

And update the two calls inside it:

```python
        total, count = await self._sum_range(day_start, day_end, exclude_task_types)
        breakdown = await self._breakdown_by_task_type(day_start, day_end, exclude_task_types)
```

- [ ] **Step 4: Update BudgetGuard to exclude external calls**

In `src/donna/cost/budget.py`, line 73, change:

```python
        daily_summary = await self._tracker.get_daily_cost()
```

to:

```python
        daily_summary = await self._tracker.get_daily_cost(
            exclude_task_types=["external_llm_call"]
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_cost_separation.py -v`
Expected: all PASS

- [ ] **Step 6: Run existing cost/budget tests to confirm no regressions**

Run: `python3 -m pytest tests/unit/ -k "cost or budget" --tb=short -q`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/donna/cost/tracker.py src/donna/cost/budget.py tests/unit/test_cost_separation.py
git commit -m "feat(llm-gateway): separate external LLM costs from Donna's budget"
```

---

### Task 6: OllamaProvider json_mode Parameter

**Files:**
- Modify: `src/donna/models/providers/ollama.py:46-75` (complete method)
- Test: existing `tests/unit/test_ollama_provider.py` or add inline

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ollama_json_mode.py
"""Test that OllamaProvider respects json_mode parameter."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.models.providers.ollama import OllamaProvider


class TestOllamaJsonMode:
    async def test_json_mode_true_includes_format(self) -> None:
        provider = OllamaProvider(base_url="http://fake:11434")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "message": {"content": '{"result": "ok"}'},
            "model": "test",
            "prompt_eval_count": 10,
            "eval_count": 5,
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(provider, "_get_session", return_value=mock_session):
            await provider.complete("test prompt", "test-model", json_mode=True)

        payload = mock_session.post.call_args[1]["json"]
        assert payload["format"] == "json"

    async def test_json_mode_false_omits_format(self) -> None:
        provider = OllamaProvider(base_url="http://fake:11434")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "message": {"content": "plain text response"},
            "model": "test",
            "prompt_eval_count": 10,
            "eval_count": 5,
        })
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(provider, "_get_session", return_value=mock_session):
            await provider.complete("test prompt", "test-model", json_mode=False)

        payload = mock_session.post.call_args[1]["json"]
        assert "format" not in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_ollama_json_mode.py -v`
Expected: FAIL with `TypeError: unexpected keyword argument 'json_mode'`

- [ ] **Step 3: Add json_mode to OllamaProvider.complete()**

In `src/donna/models/providers/ollama.py`, modify the `complete` method signature (line 46-47):

```python
    async def complete(
        self, prompt: str, model: str, max_tokens: int = 1024, json_mode: bool = True
    ) -> tuple[dict[str, Any], CompletionMetadata]:
```

And modify the payload construction (lines 66-74):

```python
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"
```

When `json_mode=False`, also skip the JSON parsing. Replace line 85 (`parsed = parse_json_response(raw_text)`) with:

```python
        if json_mode:
            parsed = parse_json_response(raw_text)
        else:
            parsed = {"text": raw_text}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_ollama_json_mode.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/models/providers/ollama.py tests/unit/test_ollama_json_mode.py
git commit -m "feat(llm-gateway): add json_mode parameter to OllamaProvider.complete()"
```

---

### Task 7: Queue Worker

This is the largest task — the core queue system.

**Files:**
- Create: `src/donna/llm/queue.py`
- Create: `tests/unit/test_llm_queue.py`

- [ ] **Step 1: Write the failing tests for queue ordering and preemption**

```python
# tests/unit/test_llm_queue.py
"""Tests for the LLM queue worker — ordering, preemption, chain handling."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.llm.alerter import GatewayAlerter
from donna.llm.queue import LLMQueueWorker
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import GatewayConfig, Priority, QueueItem
from donna.models.types import CompletionMetadata


def _make_config(**overrides) -> GatewayConfig:
    defaults = {
        "active_hours_start": 0,
        "active_hours_end": 24,  # always active for tests
        "schedule_drain_minutes": 2,
        "max_external_depth": 20,
        "max_interrupt_count": 3,
    }
    defaults.update(overrides)
    return GatewayConfig(**defaults)


def _make_meta() -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=100,
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.001,
        model_actual="ollama/test",
    )


def _make_ollama() -> AsyncMock:
    ollama = AsyncMock()
    ollama.complete = AsyncMock(return_value=({"result": "ok"}, _make_meta()))
    return ollama


class TestQueueOrdering:
    async def test_internal_before_external(self) -> None:
        """Internal items are processed before external items."""
        config = _make_config()
        ollama = _make_ollama()
        worker = LLMQueueWorker(
            config=config,
            ollama=ollama,
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        order: list[str] = []

        internal_future = asyncio.Future()
        external_future = asyncio.Future()

        async def mock_complete(prompt, model, **kwargs):
            order.append(prompt)
            return {"result": "ok"}, _make_meta()

        ollama.complete = mock_complete

        # Enqueue external first, then internal
        await worker.enqueue_external(
            prompt="external", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )
        await worker.enqueue_internal(
            prompt="internal", model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.NORMAL,
        )

        # Process two items
        await worker.process_one()
        await worker.process_one()

        assert order == ["internal", "external"]

    async def test_critical_before_normal_before_background(self) -> None:
        config = _make_config()
        ollama = _make_ollama()
        worker = LLMQueueWorker(
            config=config,
            ollama=ollama,
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        order: list[str] = []

        async def mock_complete(prompt, model, **kwargs):
            order.append(prompt)
            return {"result": "ok"}, _make_meta()

        ollama.complete = mock_complete

        # Enqueue in reverse priority order
        await worker.enqueue_internal(
            prompt="bg", model="m", max_tokens=100,
            json_mode=True, task_type="generate_nudge", priority=Priority.BACKGROUND,
        )
        await worker.enqueue_internal(
            prompt="crit", model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.CRITICAL,
        )
        await worker.enqueue_internal(
            prompt="norm", model="m", max_tokens=100,
            json_mode=True, task_type="generate_digest", priority=Priority.NORMAL,
        )

        await worker.process_one()
        await worker.process_one()
        await worker.process_one()

        assert order == ["crit", "norm", "bg"]


class TestQueueStatus:
    async def test_status_shows_queue_depths(self) -> None:
        config = _make_config()
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        await worker.enqueue_internal(
            prompt="p", model="m", max_tokens=100,
            json_mode=True, task_type="parse_task", priority=Priority.CRITICAL,
        )
        await worker.enqueue_external(
            prompt="p", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )

        status = worker.get_status()
        assert status["internal_queue"]["pending"] == 1
        assert status["external_queue"]["pending"] == 1


class TestExternalQueueLimit:
    async def test_rejects_when_full(self) -> None:
        config = _make_config(max_external_depth=2)
        worker = LLMQueueWorker(
            config=config,
            ollama=_make_ollama(),
            inv_logger=AsyncMock(),
            alerter=AsyncMock(spec=GatewayAlerter),
            rate_limiter=RateLimiter(10, 100, {}),
        )

        await worker.enqueue_external(
            prompt="1", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )
        await worker.enqueue_external(
            prompt="2", model="m", max_tokens=100,
            json_mode=True, caller="test", allow_cloud=False,
        )

        with pytest.raises(Exception, match="queue full"):
            await worker.enqueue_external(
                prompt="3", model="m", max_tokens=100,
                json_mode=True, caller="test", allow_cloud=False,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_llm_queue.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the queue worker**

```python
# src/donna/llm/queue.py
"""LLM queue worker — two-queue priority system for GPU access.

Internal queue (Donna tasks) always takes priority over external queue
(API gateway). During active hours, running external requests are
preempted. See docs/superpowers/specs/2026-04-11-llm-gateway-queue-design.md.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import structlog

from donna.llm.alerter import GatewayAlerter
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import GatewayConfig, Priority, QueueItem
from donna.models.types import CompletionMetadata

logger = structlog.get_logger()


class QueueFullError(Exception):
    """Raised when the external queue is at max depth."""


class LLMQueueWorker:
    """Two-queue worker with priority, preemption, and rate limiting.

    The worker loop calls process_one() repeatedly. Each call pops
    one item from the appropriate queue and executes it.
    """

    def __init__(
        self,
        config: GatewayConfig,
        ollama: Any,
        inv_logger: Any,
        alerter: GatewayAlerter,
        rate_limiter: RateLimiter,
        anthropic: Any | None = None,
    ) -> None:
        self._config = config
        self._ollama = ollama
        self._anthropic = anthropic
        self._inv_logger = inv_logger
        self._alerter = alerter
        self._rate_limiter = rate_limiter

        self._internal: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue()
        self._external: asyncio.Queue[QueueItem] = asyncio.Queue()
        # Front-of-queue for interrupted/continuation items
        self._external_priority: deque[QueueItem] = deque()

        self._sequence = 0
        self._current_task: QueueItem | None = None
        self._current_aio_task: asyncio.Task | None = None
        self._running = False

        # Notify event for when internal items arrive (for preemption)
        self._internal_arrived = asyncio.Event()

        # Stats (in-memory, reset on restart)
        self._stats = {
            "internal_completed": 0,
            "external_completed": 0,
            "external_interrupted": 0,
        }

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    async def enqueue_internal(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        json_mode: bool,
        task_type: str,
        priority: Priority = Priority.NORMAL,
        task_id: str | None = None,
        user_id: str = "system",
        is_chain_continuation: bool = False,
    ) -> asyncio.Future[Any]:
        """Enqueue a Donna internal LLM call. Returns a Future for the result."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        item = QueueItem(
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            json_mode=json_mode,
            future=future,
            is_internal=True,
            priority=priority,
            task_type=task_type,
            task_id=task_id,
            user_id=user_id,
            is_chain_continuation=is_chain_continuation,
            sequence=self._next_seq(),
        )

        await self._internal.put(item)
        self._internal_arrived.set()

        logger.info(
            "llm_gateway.enqueued",
            event_type="llm_gateway.enqueued",
            component="llm_gateway",
            queue="internal",
            priority=priority.name,
            task_type=task_type,
        )

        return future

    async def enqueue_external(
        self,
        prompt: str,
        model: str,
        max_tokens: int,
        json_mode: bool,
        caller: str | None,
        allow_cloud: bool,
    ) -> asyncio.Future[Any]:
        """Enqueue an external API call. Returns a Future for the result."""
        if self._external.qsize() + len(self._external_priority) >= self._config.max_external_depth:
            if self._alerter:
                await self._alerter.alert_queue_full(
                    caller or "unknown", self._config.max_external_depth
                )
            raise QueueFullError(
                f"External queue full ({self._config.max_external_depth}/{self._config.max_external_depth})"
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        item = QueueItem(
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            json_mode=json_mode,
            future=future,
            is_internal=False,
            caller=caller,
            user_id=caller or "gateway",
            allow_cloud=allow_cloud,
            sequence=self._next_seq(),
        )

        await self._external.put(item)

        # Alert if queue depth exceeds warning threshold
        total_external = self._external.qsize() + len(self._external_priority)
        if total_external >= self._config.queue_depth_warning and self._alerter:
            await self._alerter.alert_queue_depth(
                total_external, self._config.queue_depth_warning
            )

        logger.info(
            "llm_gateway.enqueued",
            event_type="llm_gateway.enqueued",
            component="llm_gateway",
            queue="external",
            caller=caller,
        )

        return future

    async def process_one(self) -> bool:
        """Pop and execute one item from the appropriate queue.

        Returns True if an item was processed, False if both queues are empty.
        """
        item = self._pop_next()
        if item is None:
            return False

        self._current_task = item
        wait_ms = int((datetime.now(timezone.utc) - item.enqueued_at).total_seconds() * 1000)

        logger.info(
            "llm_gateway.dequeued",
            event_type="llm_gateway.dequeued",
            component="llm_gateway",
            queue="internal" if item.is_internal else "external",
            priority=item.priority.name if item.is_internal else "N/A",
            caller=item.caller,
            wait_ms=wait_ms,
        )

        try:
            result, meta = await self._execute(item)

            if not item.future.cancelled():
                item.future.set_result((result, meta))

            if item.is_internal:
                self._stats["internal_completed"] += 1
            else:
                self._stats["external_completed"] += 1

            logger.info(
                "llm_gateway.completed",
                event_type="llm_gateway.completed",
                component="llm_gateway",
                queue="internal" if item.is_internal else "external",
                caller=item.caller,
                latency_ms=meta.latency_ms,
                tokens_in=meta.tokens_in,
                tokens_out=meta.tokens_out,
            )

        except asyncio.CancelledError:
            # Preempted — don't set future, item will be re-enqueued by preempt logic
            raise
        except Exception as exc:
            if not item.future.cancelled():
                item.future.set_exception(exc)

            logger.error(
                "llm_gateway.failed",
                event_type="llm_gateway.completion.failed",
                component="llm_gateway",
                caller=item.caller,
                error=str(exc),
            )
        finally:
            self._current_task = None
            self._current_aio_task = None

        return True

    def _pop_next(self) -> QueueItem | None:
        """Pop the next item according to the priority rules.

        1. Internal queue (always first)
        2. External priority deque (interrupted/continuation items)
        3. External queue (if no schedule drain needed)
        """
        # Always check internal first
        if not self._internal.empty():
            return self._internal.get_nowait()

        # Check for schedule drain — don't pop external if scheduled task imminent
        # (schedule awareness to be wired in via set_upcoming_schedule)

        # External priority items (interrupted, chain continuations)
        if self._external_priority:
            return self._external_priority.popleft()

        # Regular external queue
        if not self._external.empty():
            return self._external.get_nowait()

        return None

    async def _execute(self, item: QueueItem) -> tuple[dict, CompletionMetadata]:
        """Execute an LLM call for the given queue item."""
        result, meta = await self._ollama.complete(
            prompt=item.prompt,
            model=item.model,
            max_tokens=item.max_tokens,
            json_mode=item.json_mode,
        )
        return result, meta

    async def preempt_external(self) -> None:
        """Cancel the currently running external request and re-enqueue it."""
        if (
            self._current_task is not None
            and not self._current_task.is_internal
            and self._current_aio_task is not None
        ):
            item = self._current_task
            item.interrupted = True
            item.interrupt_count += 1
            self._stats["external_interrupted"] += 1

            self._current_aio_task.cancel()

            # Check starvation
            if item.interrupt_count >= self._config.max_interrupt_count:
                if self._alerter:
                    await self._alerter.alert_starvation(
                        item.caller or "unknown", item.interrupt_count
                    )

            # Re-enqueue at front of external priority
            self._external_priority.appendleft(item)

            logger.info(
                "llm_gateway.interrupted",
                event_type="llm_gateway.interrupted",
                component="llm_gateway",
                caller=item.caller,
                interrupt_count=item.interrupt_count,
            )

    async def run(self) -> None:
        """Main worker loop — runs for the lifetime of the process."""
        self._running = True
        logger.info("llm_queue_worker_started", event_type="system.startup")

        while self._running:
            # Check if internal items arrived while processing external
            if (
                not self._internal.empty()
                and self._current_task is not None
                and not self._current_task.is_internal
                and self._config.is_active_hours()
            ):
                await self.preempt_external()

            processed = await self.process_one()
            if not processed:
                # Both queues empty — wait a bit
                self._internal_arrived.clear()
                try:
                    await asyncio.wait_for(self._internal_arrived.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Return queue status for the /llm/queue/status endpoint."""
        current = None
        if self._current_task:
            ct = self._current_task
            current = {
                "type": "internal" if ct.is_internal else "external",
                "caller": ct.caller,
                "model": ct.model,
                "started_at": ct.enqueued_at.isoformat(),
                "task_type": ct.task_type,
            }

        # Count internal by priority
        by_priority = {"critical": 0, "normal": 0, "background": 0}
        # PriorityQueue doesn't support iteration without draining,
        # so we track counts separately
        internal_pending = self._internal.qsize()

        external_pending = self._external.qsize() + len(self._external_priority)

        return {
            "current_request": current,
            "internal_queue": {
                "pending": internal_pending,
            },
            "external_queue": {
                "pending": external_pending,
            },
            "stats_24h": {
                "internal_completed": self._stats["internal_completed"],
                "external_completed": self._stats["external_completed"],
                "external_interrupted": self._stats["external_interrupted"],
            },
            "rate_limits": self._rate_limiter.get_all_usage(),
            "mode": "active" if self._config.is_active_hours() else "slow",
        }

    def reload_config(self, config: GatewayConfig) -> None:
        """Hot-reload configuration. Preserves queue contents and counters."""
        self._config = config
        self._rate_limiter.update_limits(
            default_rpm=config.default_rpm,
            default_rph=config.default_rph,
            caller_limits=config.caller_limits,
        )
        if self._alerter:
            self._alerter.update_debounce(config.debounce_minutes)
        logger.info("llm_queue_config_reloaded", event_type="llm_gateway.config_reloaded")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_llm_queue.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/llm/queue.py tests/unit/test_llm_queue.py
git commit -m "feat(llm-gateway): add two-queue worker with priority and preemption"
```

---

### Task 8: Rewrite LLM Gateway Routes

**Files:**
- Modify: `src/donna/api/routes/llm.py` (full rewrite)
- Modify: `tests/unit/test_llm_gateway.py` (update tests)

- [ ] **Step 1: Write the updated tests**

```python
# tests/unit/test_llm_gateway.py — full replacement
"""Unit tests for the LLM gateway routes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.api.routes.llm import (
    CompletionRequest,
    CompletionResponse,
    llm_completion,
    llm_health,
    llm_models,
    llm_queue_status,
)
from donna.llm.queue import LLMQueueWorker
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import GatewayConfig
from donna.logging.invocation_logger import InvocationMetadata
from donna.models.types import CompletionMetadata


def _make_request(
    *,
    ollama: AsyncMock | None = None,
    queue: MagicMock | None = None,
    gateway_config: GatewayConfig | None = None,
) -> MagicMock:
    request = MagicMock()
    request.app.state.ollama = ollama
    request.app.state.llm_queue = queue
    request.app.state.llm_gateway_config = gateway_config or GatewayConfig()
    conn = AsyncMock()
    conn.commit = AsyncMock()
    request.app.state.db.connection = conn
    return request


def _make_meta() -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=500, tokens_in=100, tokens_out=50,
        cost_usd=0.001, model_actual="ollama/test",
    )


def test_invocation_metadata_has_gateway_fields() -> None:
    meta = InvocationMetadata(
        task_type="external_llm_call",
        model_alias="gateway/test",
        model_actual="ollama/qwen",
        input_hash="abc123",
        latency_ms=500,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        user_id="gateway",
        queue_wait_ms=1200,
        interrupted=True,
        chain_id="chain-xyz",
        caller="immich-tagger",
    )
    assert meta.queue_wait_ms == 1200
    assert meta.interrupted is True
    assert meta.chain_id == "chain-xyz"
    assert meta.caller == "immich-tagger"


class TestLLMHealth:
    async def test_health_ok(self) -> None:
        ollama = AsyncMock()
        ollama.health = AsyncMock(return_value=True)
        request = _make_request(ollama=ollama)
        result = await llm_health(request)
        assert result["ok"] is True

    async def test_health_no_provider(self) -> None:
        request = _make_request(ollama=None)
        result = await llm_health(request)
        assert result["ok"] is False


class TestLLMModels:
    async def test_list_models(self) -> None:
        ollama = AsyncMock()
        ollama.list_models = AsyncMock(return_value=["model-a", "model-b"])
        request = _make_request(ollama=ollama)
        result = await llm_models(request)
        assert result["models"] == ["model-a", "model-b"]


class TestQueueStatus:
    async def test_returns_status(self) -> None:
        queue = MagicMock()
        queue.get_status.return_value = {
            "current_request": None,
            "internal_queue": {"pending": 0},
            "external_queue": {"pending": 0},
            "stats_24h": {},
            "rate_limits": {},
            "mode": "active",
        }
        request = _make_request(queue=queue)
        result = await llm_queue_status(request)
        assert result["mode"] == "active"
```

- [ ] **Step 2: Rewrite the gateway routes**

```python
# src/donna/api/routes/llm.py — full replacement
"""LLM Gateway routes — expose local Ollama to other homelab services.

Requests are enqueued into the priority queue system. Donna's internal
tasks always take priority. External requests are rate-limited and
budget-checked. See docs/superpowers/specs/2026-04-11-llm-gateway-queue-design.md.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from donna.llm.queue import QueueFullError

logger = structlog.get_logger()

router = APIRouter()


def _require_api_key(
    request: Request,
    x_api_key: str | None = Header(None),
) -> None:
    """Validate API key from gateway config."""
    config = getattr(request.app.state, "llm_gateway_config", None)
    api_key = config.api_key if config else ""
    if not api_key:
        return
    if x_api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class CompletionRequest(BaseModel):
    prompt: str
    model: str | None = Field(default=None, description="Ollama model tag.")
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    json_mode: bool = Field(default=True, description="Request JSON output.")
    caller: str | None = Field(default=None, description="Calling service identifier.")
    allow_cloud: bool = Field(default=False, description="Allow Claude fallback.")


class CompletionResponse(BaseModel):
    output: Any
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


def _resolve_model(request: Request, requested: str | None) -> str:
    """Resolve model from request or gateway config default."""
    if requested:
        return requested
    config = getattr(request.app.state, "llm_gateway_config", None)
    if config:
        models_cfg = getattr(request.app.state, "models_config", None) or {}
        default_alias = "local_parser"
        model_entry = models_cfg.get("models", {}).get(default_alias, {})
        return model_entry.get("model", "qwen2.5:32b-instruct-q6_K")
    return "qwen2.5:32b-instruct-q6_K"


@router.get("/health")
async def llm_health(request: Request) -> dict[str, Any]:
    """Check if the Ollama backend is reachable."""
    ollama = getattr(request.app.state, "ollama", None)
    if ollama is None:
        return {"ok": False, "detail": "Ollama provider not initialised"}
    ok = await ollama.health()
    return {"ok": ok}


@router.get("/models")
async def llm_models(request: Request) -> dict[str, Any]:
    """List locally available models."""
    ollama = getattr(request.app.state, "ollama", None)
    if ollama is None:
        return {"models": [], "detail": "Ollama provider not initialised"}
    models = await ollama.list_models()
    return {"models": models}


@router.get("/queue/status")
async def llm_queue_status(request: Request) -> dict[str, Any]:
    """Live queue status for the dashboard."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        return {"error": "Queue worker not initialised"}
    return queue.get_status()


@router.post("/completions", dependencies=[Depends(_require_api_key)])
async def llm_completion(
    body: CompletionRequest,
    request: Request,
) -> CompletionResponse:
    """Enqueue a completion request. Blocks until result is ready."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")

    # Rate limit check
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    if rate_limiter and body.caller:
        if not rate_limiter.check(body.caller):
            config = request.app.state.llm_gateway_config
            alerter = getattr(request.app.state, "gateway_alerter", None)
            if alerter:
                rejections = rate_limiter.recent_rejections(body.caller)
                if rejections >= config.rate_limit_alert_threshold:
                    usage = rate_limiter.get_usage(body.caller)
                    await alerter.alert_rate_limited(
                        body.caller, usage["minute_count"], usage["minute_limit"]
                    )
            raise HTTPException(
                429,
                detail="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

    model = _resolve_model(request, body.model)
    max_tokens = min(body.max_tokens, request.app.state.llm_gateway_config.max_external_depth)

    try:
        future = await queue.enqueue_external(
            prompt=body.prompt,
            model=model,
            max_tokens=body.max_tokens,
            json_mode=body.json_mode,
            caller=body.caller,
            allow_cloud=body.allow_cloud,
        )
    except QueueFullError as exc:
        raise HTTPException(
            503,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc

    try:
        result, meta = await future
    except asyncio.CancelledError:
        raise HTTPException(504, "Request was preempted and not completed")
    except Exception as exc:
        raise HTTPException(502, f"LLM error: {exc}") from exc

    return CompletionResponse(
        output=result,
        model=model,
        tokens_in=meta.tokens_in,
        tokens_out=meta.tokens_out,
        latency_ms=meta.latency_ms,
    )
```

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/unit/test_llm_gateway.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/donna/api/routes/llm.py tests/unit/test_llm_gateway.py
git commit -m "refactor(llm-gateway): rewrite routes to use queue system"
```

---

### Task 9: Wire Everything Into FastAPI Lifespan

**Files:**
- Modify: `src/donna/api/__init__.py` (lifespan and middleware)
- Modify: `src/donna/api/routes/admin_config.py` (hot-reload hook, allowed configs)
- Modify: `src/donna/api/routes/admin_health.py` (config flag for Ollama)
- Modify: `config/donna_models.yaml` (remove llm_gateway section)

- [ ] **Step 1: Update lifespan to create queue worker**

In `src/donna/api/__init__.py`, update imports to add:

```python
from donna.llm.alerter import GatewayAlerter
from donna.llm.queue import LLMQueueWorker
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import load_gateway_config
from donna.logging.invocation_logger import InvocationLogger
```

Update the lifespan function — replace the gateway config and Ollama section with:

```python
    # Load gateway config
    gw_config = load_gateway_config(config_dir)
    app.state.llm_gateway_config = gw_config

    # Initialise OllamaProvider
    ollama_cfg = models_config.get("ollama", {})
    ollama_url = os.environ.get(
        "DONNA_OLLAMA_URL",
        ollama_cfg.get("base_url", "http://donna-ollama:11434"),
    )
    ollama = OllamaProvider(
        base_url=ollama_url,
        timeout_s=int(ollama_cfg.get("timeout_s", 120)),
    )
    app.state.ollama = ollama

    # Rate limiter
    rate_limiter = RateLimiter(
        default_rpm=gw_config.default_rpm,
        default_rph=gw_config.default_rph,
        caller_limits=gw_config.caller_limits,
    )
    app.state.rate_limiter = rate_limiter

    # Gateway alerter (notifier will be set later when Discord bot is available)
    async def _debug_log_notifier(channel: str, message: str) -> None:
        logger.info("gateway_alert", channel=channel, message=message,
                     event_type="llm_gateway.alert")

    alerter = GatewayAlerter(
        notifier=_debug_log_notifier,
        debounce_minutes=gw_config.debounce_minutes,
    )
    app.state.gateway_alerter = alerter

    # LLM Queue Worker
    inv_logger = InvocationLogger(db.connection)
    queue_worker = LLMQueueWorker(
        config=gw_config,
        ollama=ollama,
        inv_logger=inv_logger,
        alerter=alerter,
        rate_limiter=rate_limiter,
    )
    app.state.llm_queue = queue_worker

    # Start worker as background task
    worker_task = asyncio.create_task(queue_worker.run())

    logger.info("donna_api_started", db_path=str(db_path), port=8200)
    yield

    await queue_worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await ollama.close()
    await db.close()
    logger.info("donna_api_stopped")
```

- [ ] **Step 2: Update middleware to exclude health paths**

In `RequestLoggingMiddleware.dispatch()`, add at the top of the method:

```python
        # Skip logging for health check endpoints
        if request.url.path in ("/health", "/admin/health"):
            return await call_next(request)
```

- [ ] **Step 3: Add llm_gateway.yaml to allowed configs and add hot-reload hook**

In `src/donna/api/routes/admin_config.py`, add `"llm_gateway.yaml"` to `_ALLOWED_CONFIGS` set.

In the `put_config` function, after the file is written (after line 128 `stat = path.stat()`), add:

```python
    # Hot-reload hook for gateway config
    if filename == "llm_gateway.yaml":
        from donna.llm.types import load_gateway_config
        new_config = load_gateway_config(config_dir)
        queue = getattr(request.app.state, "llm_queue", None)
        if queue:
            queue.reload_config(new_config)
        request.app.state.llm_gateway_config = new_config
```

- [ ] **Step 4: Update admin_health to use config flag**

In `src/donna/api/routes/admin_health.py`, change `_check_ollama()` to accept the config flag:

```python
async def _check_ollama(check_enabled: bool = True) -> dict[str, Any] | None:
    if not check_enabled or not _OLLAMA_URL:
        return None
```

In `admin_health()`, read the flag:

```python
    gw_config = getattr(request.app.state, "llm_gateway_config", None)
    ollama_check_enabled = gw_config.ollama_health_check if gw_config else True

    db_check, loki_check, ollama_check = await asyncio.gather(
        _check_db(conn),
        _check_loki(),
        _check_ollama(check_enabled=ollama_check_enabled),
    )
```

- [ ] **Step 5: Remove llm_gateway section from donna_models.yaml**

In `config/donna_models.yaml`, remove the `llm_gateway:` section (5 lines added in the earlier implementation).

- [ ] **Step 6: Run all affected tests**

Run: `python3 -m pytest tests/unit/test_llm_gateway.py tests/unit/test_llm_queue.py tests/unit/test_admin_health.py tests/unit/test_admin_config.py -v --tb=short`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/donna/api/__init__.py src/donna/api/routes/admin_config.py src/donna/api/routes/admin_health.py config/donna_models.yaml
git commit -m "feat(llm-gateway): wire queue worker into FastAPI lifespan with hot-reload"
```

---

### Task 10: Bug Fixes

**Files:**
- Modify: `scripts/donna-up.sh`
- Modify: `scripts/donna-down.sh`
- Modify: `src/donna/api/routes/admin_logs.py`

- [ ] **Step 1: Add --env-file to startup scripts**

In `scripts/donna-up.sh`, update each `docker compose` line to include `--env-file "$DOCKER_DIR/.env"`:

```bash
docker compose -f "$DOCKER_DIR/docker-compose.yml" --env-file "$DOCKER_DIR/.env" up -d
```

```bash
docker compose -f "$DOCKER_DIR/donna-monitoring.yml" --env-file "$DOCKER_DIR/.env" up -d
```

```bash
docker compose -f "$DOCKER_DIR/donna-ollama.yml" --env-file "$DOCKER_DIR/.env" up -d
```

```bash
docker compose -f "$DOCKER_DIR/donna-app.yml" --env-file "$DOCKER_DIR/.env" up -d
```

```bash
docker compose -f "$DOCKER_DIR/donna-ui.yml" --env-file "$DOCKER_DIR/.env" up -d
```

In `scripts/donna-down.sh`, same pattern for each line:

```bash
docker compose -f "$DOCKER_DIR/donna-ui.yml" --env-file "$DOCKER_DIR/.env" down 2>/dev/null || true
```

(Repeat for all 5 compose commands.)

- [ ] **Step 2: Update llm_gateway event types in admin_logs.py**

In `src/donna/api/routes/admin_logs.py`, update the `llm_gateway` entry in `EVENT_TYPE_TREE`:

```python
    "llm_gateway": [
        "enqueued", "dequeued", "interrupted", "completed",
        "rejected", "drain_started", "config_reloaded", "alert",
    ],
```

- [ ] **Step 3: Run lint on changed files**

Run: `ruff check scripts/donna-up.sh scripts/donna-down.sh src/donna/api/routes/admin_logs.py 2>&1`

- [ ] **Step 4: Commit**

```bash
git add scripts/donna-up.sh scripts/donna-down.sh src/donna/api/routes/admin_logs.py
git commit -m "fix(llm-gateway): add --env-file to scripts, update event types"
```

---

### Task 11: Full Integration Test

- [ ] **Step 1: Run the full unit test suite**

Run: `python3 -m pytest tests/unit/ -m "not slow and not llm" --tb=short -q`
Expected: new tests pass, no regressions beyond pre-existing failures

- [ ] **Step 2: Run ruff on all changed files**

Run: `ruff check src/donna/llm/ src/donna/api/ src/donna/cost/ src/donna/models/providers/ollama.py src/donna/logging/invocation_logger.py`
Expected: no errors (or only pre-existing ones)

- [ ] **Step 3: Fix any issues found**

- [ ] **Step 4: Final commit**

```bash
git commit -m "chore(llm-gateway): fix lint issues" --allow-empty
```
