"""API resilience layer.

Every Claude API call goes through this wrapper. Handles retries,
degraded mode fallback, and circuit breaking. See docs/resilience.md.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

import structlog

logger = structlog.get_logger()


class TaskCategory(str, Enum):
    """Retry policy categories. See docs/resilience.md Section 3.6.1."""

    CRITICAL = "critical"   # digest, deadline reminders: 3 retries, degraded fallback
    STANDARD = "standard"   # parse, classify: 2 retries, queue for later
    AGENT = "agent"         # research, code gen: 1 retry, fail fast (budget protection)


@dataclass
class RetryPolicy:
    """Retry configuration for a task category."""

    max_retries: int
    base_delay_s: float
    max_delay_s: float
    exponential: bool = True


RETRY_POLICIES: dict[TaskCategory, RetryPolicy] = {
    TaskCategory.CRITICAL: RetryPolicy(max_retries=3, base_delay_s=2.0, max_delay_s=30.0),
    TaskCategory.STANDARD: RetryPolicy(max_retries=2, base_delay_s=1.0, max_delay_s=15.0),
    TaskCategory.AGENT: RetryPolicy(max_retries=1, base_delay_s=5.0, max_delay_s=5.0, exponential=False),
}


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, all calls short-circuited
    HALF_OPEN = "half_open" # Testing recovery


@dataclass
class CircuitBreaker:
    """Circuit breaker for API calls.

    Opens after `failure_threshold` consecutive failures within `window_s`.
    Tests recovery every `recovery_interval_s`.
    See docs/resilience.md Section 3.6.3.
    """

    failure_threshold: int = 5
    window_s: float = 600.0  # 10 minutes
    recovery_interval_s: float = 300.0  # 5 minutes

    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    first_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.monotonic)

    def record_success(self) -> None:
        """Record a successful call. Resets circuit breaker."""
        if self.state != CircuitBreakerState.CLOSED:
            logger.info("circuit_breaker_closed", previous_state=self.state.value)
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.first_failure_time = 0.0
        self.last_state_change = time.monotonic()

    def record_failure(self) -> None:
        """Record a failed call. May open circuit breaker."""
        now = time.monotonic()

        if self.failure_count == 0:
            self.first_failure_time = now

        # Reset counter if window has elapsed
        if now - self.first_failure_time > self.window_s:
            self.failure_count = 1
            self.first_failure_time = now
            return

        self.failure_count += 1

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            self.last_state_change = now
            logger.critical(
                "circuit_breaker_opened",
                failure_count=self.failure_count,
                window_s=self.window_s,
            )

    def should_allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        if self.state == CircuitBreakerState.CLOSED:
            return True

        if self.state == CircuitBreakerState.OPEN:
            # Check if recovery interval has elapsed
            elapsed = time.monotonic() - self.last_state_change
            if elapsed >= self.recovery_interval_s:
                self.state = CircuitBreakerState.HALF_OPEN
                self.last_state_change = time.monotonic()
                logger.info("circuit_breaker_half_open", elapsed_s=elapsed)
                return True  # Allow one test request
            return False

        # HALF_OPEN: allow the test request
        return True


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open and blocking requests."""

    pass


async def resilient_call(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    category: TaskCategory = TaskCategory.STANDARD,
    circuit_breaker: CircuitBreaker | None = None,
    **kwargs: Any,
) -> Any:
    """Execute an async function with retry logic and circuit breaker.

    Args:
        func: Async function to call
        category: Retry policy category
        circuit_breaker: Optional shared circuit breaker instance

    Returns:
        The function's return value

    Raises:
        CircuitBreakerOpenError: If circuit breaker is open
        Exception: The last exception if all retries exhausted
    """
    policy = RETRY_POLICIES[category]

    if circuit_breaker and not circuit_breaker.should_allow_request():
        raise CircuitBreakerOpenError(
            "Circuit breaker is open. API calls are blocked."
        )

    last_exception: Exception | None = None

    for attempt in range(policy.max_retries + 1):
        try:
            result = await func(*args, **kwargs)
            if circuit_breaker:
                circuit_breaker.record_success()
            return result
        except Exception as e:
            last_exception = e

            if circuit_breaker:
                circuit_breaker.record_failure()

            if attempt < policy.max_retries:
                if policy.exponential:
                    delay = min(
                        policy.base_delay_s * (2 ** attempt),
                        policy.max_delay_s,
                    )
                else:
                    delay = policy.base_delay_s

                logger.warning(
                    "api_call_retrying",
                    attempt=attempt + 1,
                    max_retries=policy.max_retries,
                    delay_s=delay,
                    category=category.value,
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "api_call_failed_all_retries",
                    category=category.value,
                    attempts=attempt + 1,
                    error_type=type(e).__name__,
                )

    raise last_exception  # type: ignore[misc]
