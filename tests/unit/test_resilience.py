"""Unit tests for the API resilience layer.

Tests retry logic and circuit breaker behavior.
"""


import pytest

from donna.resilience.retry import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    TaskCategory,
    resilient_call,
)


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.should_allow_request()

    def test_opens_after_threshold_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, window_s=600)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert not cb.should_allow_request()

    def test_success_resets_circuit_breaker(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, window_s=600)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.failure_count == 0

    def test_success_closes_open_breaker(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, window_s=600)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_half_open_after_recovery_interval(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, window_s=600, recovery_interval_s=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        # Wait for recovery interval
        import time
        time.sleep(0.02)
        assert cb.should_allow_request()
        assert cb.state == CircuitBreakerState.HALF_OPEN


class TestResilientCall:
    @pytest.mark.asyncio
    async def test_successful_call(self) -> None:
        async def success() -> str:
            return "ok"

        result = await resilient_call(success, category=TaskCategory.STANDARD)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        call_count = 0

        async def fail_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("temporary failure")
            return "recovered"

        result = await resilient_call(
            fail_then_succeed, category=TaskCategory.STANDARD
        )
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries(self) -> None:
        async def always_fail() -> str:
            raise ConnectionError("persistent failure")

        with pytest.raises(ConnectionError):
            await resilient_call(
                always_fail, category=TaskCategory.AGENT  # 1 retry only
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_calls(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, window_s=600)
        cb.record_failure()
        cb.record_failure()

        async def should_not_run() -> str:
            return "should not reach here"

        with pytest.raises(CircuitBreakerOpenError):
            await resilient_call(
                should_not_run,
                category=TaskCategory.STANDARD,
                circuit_breaker=cb,
            )

    @pytest.mark.asyncio
    async def test_critical_retries_more_than_agent(self) -> None:
        """Critical tasks get 3 retries, agent tasks get 1."""
        critical_count = 0
        agent_count = 0

        async def count_critical() -> str:
            nonlocal critical_count
            critical_count += 1
            raise ConnectionError("fail")

        async def count_agent() -> str:
            nonlocal agent_count
            agent_count += 1
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError):
            await resilient_call(count_critical, category=TaskCategory.CRITICAL)
        with pytest.raises(ConnectionError):
            await resilient_call(count_agent, category=TaskCategory.AGENT)

        assert critical_count == 4  # 1 initial + 3 retries
        assert agent_count == 2    # 1 initial + 1 retry
