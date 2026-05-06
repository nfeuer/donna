"""ToolGapSurfacer — single sink for every tool-gap detection point.

Slice 22 routes every detected gap through this class. It records the
row, writes the audit, and (for high-severity gaps) posts the
:class:`donna.integrations.discord_views.ToolGapPingView` to Discord.
Speculative gaps are silent — :class:`donna.notifications.digest.MorningDigest`
picks them up the next morning.

Re-emission of the same ``(user_id, tool_name)`` while the row is open
is deduped at the repository layer (priority bump, rationale refresh,
``last_seen_at`` touched). The surfacer additionally rate-limits Discord
re-pings to avoid spam — a gap is re-pinged at most once per
``REPING_COOLDOWN_SECONDS`` (default 4h).

Realizes docs/superpowers/specs/manual-escalation.md §7.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog

from donna.cost.tool_gap import (
    SEVERITY_HIGH,
    ToolGap,
)
from donna.cost.tool_gap_audit import (
    EVENT_TOOL_GAP_DETECTED,
    EVENT_TOOL_REQUEST_FILED,
    write_tool_gap_event,
)
from donna.cost.tool_request_repository import (
    RecordResult,
    ToolRequestRepository,
    ToolRequestRow,
)

logger = structlog.get_logger()

REPING_COOLDOWN_SECONDS = 14_400  # 4 hours


class ToolGapPingPoster(Protocol):
    """Posts a ``[File request] [Snooze 24h]`` view to Discord.

    The concrete implementation lives in cli_wiring and closes over
    the bot, owner Discord ID, gate, repo, and target channel name.
    Returns True if the message was posted.
    """

    async def __call__(self, row: ToolRequestRow) -> bool: ...  # pragma: no cover


class ToolGapSurfacer:
    """Single dispatch point for every detection site."""

    def __init__(
        self,
        *,
        repository: ToolRequestRepository,
        conn: Any,
        ping_poster: ToolGapPingPoster | None = None,
        reping_cooldown_seconds: int = REPING_COOLDOWN_SECONDS,
    ) -> None:
        self._repo = repository
        self._conn = conn
        self._ping_poster = ping_poster
        self._reping_cooldown_seconds = reping_cooldown_seconds

    def set_ping_poster(self, poster: ToolGapPingPoster | None) -> None:
        """Late-binding setter for cli_wiring.

        The surfacer is constructed early (before the bot exists) so
        boot-time speculative gaps can be filed; the bot-aware ping
        poster is bolted on after the bot is alive.
        """
        self._ping_poster = poster

    async def surface(
        self,
        gap: ToolGap,
        *,
        now: datetime | None = None,
    ) -> ToolRequestRow:
        """Record + audit + (high) post.

        Returns the persisted row regardless of severity.
        """
        result: RecordResult = await self._repo.record(gap, now=now)
        row = result.row

        await write_tool_gap_event(
            self._conn,
            event=EVENT_TOOL_GAP_DETECTED,
            tool_request_id=row.id,
            user_id=row.user_id,
            payload={
                "tool_name": row.tool_name,
                "severity": row.severity,
                "blocking_capability_id": row.blocking_capability_id,
                "detection_point": row.detection_point,
                "rationale": row.rationale,
                "is_new": result.is_new,
            },
            now=now,
        )

        if row.severity != SEVERITY_HIGH:
            logger.info(
                "tool_gap_surfaced_speculative",
                tool_request_id=row.id,
                tool_name=row.tool_name,
                detection_point=row.detection_point,
            )
            return row

        # ---- High-severity branch ----
        if self._ping_poster is None:
            logger.warning(
                "tool_gap_high_no_poster",
                tool_request_id=row.id,
                tool_name=row.tool_name,
            )
            return row

        if not self._should_reping(row, now=now):
            logger.info(
                "tool_gap_reping_suppressed_cooldown",
                tool_request_id=row.id,
                tool_name=row.tool_name,
                last_pinged_at=row.last_pinged_at,
            )
            return row

        try:
            posted = await self._ping_poster(row)
        except Exception:
            logger.exception(
                "tool_gap_ping_post_failed",
                tool_request_id=row.id,
                tool_name=row.tool_name,
            )
            return row

        if posted:
            await self._repo.mark_pinged(row.id, now=now)
            await write_tool_gap_event(
                self._conn,
                event=EVENT_TOOL_REQUEST_FILED,
                tool_request_id=row.id,
                user_id=row.user_id,
                payload={
                    "tool_name": row.tool_name,
                    "channel": "agents",
                    "reping": not result.is_new,
                },
                now=now,
            )
            logger.info(
                "tool_gap_pinged",
                tool_request_id=row.id,
                tool_name=row.tool_name,
            )
        return row

    def _should_reping(
        self, row: ToolRequestRow, *, now: datetime | None
    ) -> bool:
        if row.last_pinged_at is None:
            return True
        cutoff = (now or datetime.now(tz=UTC)) - timedelta(
            seconds=self._reping_cooldown_seconds
        )
        return row.last_pinged_at < cutoff
