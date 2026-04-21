"""LLM queue worker — two-queue priority system for GPU access.

Internal queue (Donna tasks) always takes priority over external queue
(API gateway). During active hours, running external requests are
preempted. See docs/superpowers/specs/archive/2026-04-11-llm-gateway-queue-design.md.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from datetime import UTC, datetime
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

        # Condition broadcast for SSE — notified on every state change
        self.state_changed = asyncio.Condition()

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

        async with self.state_changed:
            self.state_changed.notify_all()

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
            max_d = self._config.max_external_depth
            raise QueueFullError(f"External queue full ({max_d}/{max_d})")

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

        async with self.state_changed:
            self.state_changed.notify_all()

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
        wait_ms = int((datetime.now(UTC) - item.enqueued_at).total_seconds() * 1000)

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
                async with self.state_changed:
                    self.state_changed.notify_all()

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
                async with self.state_changed:
                    self.state_changed.notify_all()

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
            if item.interrupt_count >= self._config.max_interrupt_count and self._alerter:
                await self._alerter.alert_starvation(
                    item.caller or "unknown", item.interrupt_count
                )

            # Re-enqueue at front of external priority
            self._external_priority.appendleft(item)
            async with self.state_changed:
                self.state_changed.notify_all()

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
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._internal_arrived.wait(), timeout=0.1)

    async def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Return queue status for the /llm/queue/status endpoint."""
        current = None
        if self._current_task:
            ct = self._current_task
            current = {
                "sequence": ct.sequence,
                "type": "internal" if ct.is_internal else "external",
                "caller": ct.caller,
                "model": ct.model,
                "started_at": ct.enqueued_at.isoformat(),
                "task_type": ct.task_type,
                "prompt_preview": ct.prompt[:100],
            }

        internal_pending = self._internal.qsize()
        external_pending = self._external.qsize() + len(self._external_priority)

        return {
            "current_request": current,
            "internal_queue": {
                "pending": internal_pending,
                "next_items": self._peek_internal(2),
            },
            "external_queue": {
                "pending": external_pending,
                "next_items": self._peek_external(2),
            },
            "stats_24h": {
                "internal_completed": self._stats["internal_completed"],
                "external_completed": self._stats["external_completed"],
                "external_interrupted": self._stats["external_interrupted"],
            },
            "rate_limits": self._rate_limiter.get_all_usage(),
            "mode": "active" if self._config.is_active_hours() else "slow",
        }

    def _peek_internal(self, n: int) -> list[dict[str, Any]]:
        """Peek at next N items in the internal PriorityQueue without removing them."""
        items: list[QueueItem] = []
        while not self._internal.empty() and len(items) < n:
            items.append(self._internal.get_nowait())
        result = [self._item_preview(it) for it in items]
        for it in items:
            self._internal.put_nowait(it)
        return result

    def _peek_external(self, n: int) -> list[dict[str, Any]]:
        """Peek at next N items from external priority deque + external queue."""
        result: list[dict[str, Any]] = []
        for it in list(self._external_priority)[:n]:
            result.append(self._item_preview(it))
        remaining = n - len(result)
        if remaining <= 0:
            return result
        items: list[QueueItem] = []
        while not self._external.empty() and len(items) < remaining:
            items.append(self._external.get_nowait())
        result.extend(self._item_preview(it) for it in items)
        for it in items:
            self._external.put_nowait(it)
        return result

    def _item_preview(self, item: QueueItem) -> dict[str, Any]:
        """Build a preview dict for a queue item."""
        return {
            "sequence": item.sequence,
            "caller": item.caller,
            "model": item.model,
            "task_type": item.task_type,
            "enqueued_at": item.enqueued_at.isoformat(),
            "prompt_preview": item.prompt[:100],
        }

    def get_item(self, sequence: int) -> dict[str, Any] | None:
        """Return full details for a queued or in-progress item by sequence number."""
        # Check current task
        if self._current_task and self._current_task.sequence == sequence:
            return self._item_full(self._current_task)

        # Check internal queue (drain + refill)
        found = None
        items: list[QueueItem] = []
        while not self._internal.empty():
            it = self._internal.get_nowait()
            items.append(it)
            if it.sequence == sequence:
                found = it
        for it in items:
            self._internal.put_nowait(it)
        if found:
            return self._item_full(found)

        # Check external priority deque
        for it in self._external_priority:
            if it.sequence == sequence:
                return self._item_full(it)

        # Check external queue (drain + refill)
        items = []
        while not self._external.empty():
            it = self._external.get_nowait()
            items.append(it)
            if it.sequence == sequence:
                found = it
        for it in items:
            self._external.put_nowait(it)
        if found:
            return self._item_full(found)

        return None

    def _item_full(self, item: QueueItem) -> dict[str, Any]:
        """Build a full detail dict for a queue item."""
        return {
            "sequence": item.sequence,
            "type": "internal" if item.is_internal else "external",
            "caller": item.caller,
            "model": item.model,
            "task_type": item.task_type,
            "enqueued_at": item.enqueued_at.isoformat(),
            "prompt": item.prompt,
            "max_tokens": item.max_tokens,
            "json_mode": item.json_mode,
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
