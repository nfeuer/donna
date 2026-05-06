"""Hourly nag for tools that are built but unrebuilt (spec §10.5 row 1).

Slice 22's ``tool_lint/metadata.py`` flags ``requires_rebuild = True``
as a *warning* on the validation panel; it does NOT block submission
because the merge happens manually. After merge, the user has to
``docker compose build`` + restart so the new tool's dependencies
land in the orchestrator image. This nagger pings them once an hour
until the tool name appears in :class:`ToolRegistry.list_tool_names`,
which is the only signal we have that the new build picked up.

The followup ``S22 — requires_rebuild=True Discord nag deferred``
in ``docs/superpowers/specs/followups.md`` calls this module out as
the slice-24 deliverable. The implementation is intentionally
small — one query, a name set diff, and a delegate poster the bot
wiring layer fills in (mirrors :class:`ToolGapPingPoster`).

Realises docs/superpowers/specs/manual-escalation.md §10.5 row 1.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog

from donna.cost.tool_request_repository import (
    ToolRequestRepository,
    ToolRequestRow,
)

logger = structlog.get_logger()


# Default cooldown — once an hour. Configurable via the constructor
# so the test runs fast and an operator can tune live.
DEFAULT_NAG_INTERVAL_SECONDS = 3600
DEFAULT_GRACE_SECONDS = 3600  # only nag rows resolved over an hour ago


class RequiresRebuildNagPoster(Protocol):
    """Posts the nag message to Discord.

    Concrete implementation in cli_wiring closes over the bot, owner
    Discord ID, and target channel name. Returns True if the post
    landed; the nagger then stamps ``last_pinged_at`` so the next
    tick respects the cooldown.
    """

    async def __call__(
        self, row: ToolRequestRow
    ) -> bool: ...  # pragma: no cover


_RegisteredToolsProvider = Callable[[], Iterable[str]] | Callable[
    [], Awaitable[Iterable[str]]
]


class RequiresRebuildNagger:
    """Tick-driven scanner for unrebuilt completed tool_requests.

    The orchestrator wires this to a 60-second tick (matching the
    other escalation loops). The nagger consults the live
    :class:`ToolRegistry`'s tool-name set on each tick: a row whose
    ``tool_name`` is missing from that set after the grace window
    expired is a candidate for the nag, subject to the per-row
    cooldown so we don't spam.
    """

    def __init__(
        self,
        *,
        repository: ToolRequestRepository,
        registered_tools_provider: _RegisteredToolsProvider,
        ping_poster: RequiresRebuildNagPoster,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        nag_interval_seconds: int = DEFAULT_NAG_INTERVAL_SECONDS,
    ) -> None:
        self._repo = repository
        self._provider = registered_tools_provider
        self._poster = ping_poster
        self._grace_seconds = grace_seconds
        self._nag_interval_seconds = nag_interval_seconds

    async def _live_tools(self) -> set[str]:
        result = self._provider()
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[misc]
        return set(result)

    async def tick_once(self, *, now: datetime | None = None) -> int:
        """One pass — returns the number of nags posted.

        Skips:
        - rows whose ``tool_name`` is already registered (rebuild
          shipped),
        - rows whose ``last_pinged_at`` is younger than
          ``nag_interval_seconds`` (cooldown),
        - rows resolved within the grace window (still fresh; user
          needs a few minutes to ``compose build`` + restart).
        """
        ts = now or datetime.now(tz=UTC)
        cutoff = ts - timedelta(seconds=self._grace_seconds)
        candidates = await self._repo.list_completed_resolved_before(cutoff=cutoff)
        if not candidates:
            return 0

        live = await self._live_tools()
        cooldown = timedelta(seconds=self._nag_interval_seconds)
        posted = 0
        for row in candidates:
            if row.tool_name in live:
                continue  # rebuild took effect
            if row.last_pinged_at is not None and (
                ts - row.last_pinged_at
            ) < cooldown:
                continue
            try:
                ok = await self._poster(row)
            except Exception:
                logger.exception(
                    "requires_rebuild_nag_post_failed",
                    tool_request_id=row.id,
                    tool_name=row.tool_name,
                )
                continue
            if ok:
                await self._repo.mark_pinged(row.id, now=ts)
                logger.info(
                    "requires_rebuild_nag_posted",
                    tool_request_id=row.id,
                    tool_name=row.tool_name,
                )
                posted += 1
        return posted
