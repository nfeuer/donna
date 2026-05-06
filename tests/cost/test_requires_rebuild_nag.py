"""Slice 24 — RequiresRebuildNagger (spec §10.5 row 1).

Pin the contract spelled out in S22 followup:

  *Slice 24 — add an hourly job that scans
  ``tool_request WHERE status='completed' AND resolved_at < now-1h``
  and posts a "Tool ``X`` is built but the orchestrator hasn't been
  restarted yet" reminder until the tool name appears in
  ``ToolRegistry.list_tool_names()`` after boot.*

The harness exercises the four predicates the production loop will
make decisions on: cooldown, grace window, live-registry hit, and
poster failure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from donna.cost.requires_rebuild_nag import RequiresRebuildNagger
from donna.cost.tool_request_repository import ToolRequestRepository

_SCHEMA = """
CREATE TABLE tool_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    proposed_signature TEXT,
    rationale TEXT,
    blocking_capability_id TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL DEFAULT 'speculative',
    detection_point TEXT,
    snoozed_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_branch TEXT,
    escalation_request_id INTEGER,
    last_pinged_at TEXT
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "rebuild_nag.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> ToolRequestRepository:
    return ToolRequestRepository(conn)


async def _seed_completed(
    conn: aiosqlite.Connection,
    *,
    tool_name: str,
    resolved_at: datetime,
    last_pinged_at: datetime | None = None,
    user_id: str = "nick",
) -> int:
    cur = await conn.execute(
        """
        INSERT INTO tool_request (
            user_id, tool_name, status, severity,
            first_seen_at, last_seen_at, created_at,
            resolved_at, last_pinged_at
        ) VALUES (?, ?, 'completed', 'high', ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            tool_name,
            resolved_at.isoformat(),
            resolved_at.isoformat(),
            resolved_at.isoformat(),
            resolved_at.isoformat(),
            last_pinged_at.isoformat() if last_pinged_at else None,
        ),
    )
    await conn.commit()
    return int(cur.lastrowid or 0)


class TestRequiresRebuildNagger:
    async def test_nags_when_tool_unregistered_and_grace_elapsed(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(hours=2),
        )
        poster = AsyncMock(return_value=True)
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=lambda: [],  # nothing registered
            ping_poster=poster,
        )

        posted = await nagger.tick_once(now=now)
        assert posted == 1
        poster.assert_awaited_once()
        # ``last_pinged_at`` got stamped so the next tick respects cooldown.
        cur = await conn.execute(
            "SELECT last_pinged_at FROM tool_request WHERE tool_name = ?",
            ("fetch_url",),
        )
        row = await cur.fetchone()
        assert row[0] is not None

    async def test_skips_when_tool_now_registered(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(hours=2),
        )
        poster = AsyncMock(return_value=True)
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=lambda: ["fetch_url", "send_email"],
            ping_poster=poster,
        )

        assert await nagger.tick_once(now=now) == 0
        poster.assert_not_awaited()

    async def test_skips_within_grace_window(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        """Resolved 5 minutes ago — user is still mid-restart. Don't nag."""
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(minutes=5),
        )
        poster = AsyncMock(return_value=True)
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=lambda: [],
            ping_poster=poster,
            grace_seconds=3600,  # 1 hour grace
        )

        assert await nagger.tick_once(now=now) == 0
        poster.assert_not_awaited()

    async def test_respects_cooldown(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        """Already pinged 30 minutes ago — within 1h cooldown. Skip."""
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(hours=4),
            last_pinged_at=now - timedelta(minutes=30),
        )
        poster = AsyncMock(return_value=True)
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=lambda: [],
            ping_poster=poster,
            nag_interval_seconds=3600,
        )

        assert await nagger.tick_once(now=now) == 0
        poster.assert_not_awaited()

    async def test_re_nags_after_cooldown(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(hours=4),
            last_pinged_at=now - timedelta(hours=2),  # 2h > 1h cooldown
        )
        poster = AsyncMock(return_value=True)
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=lambda: [],
            ping_poster=poster,
            nag_interval_seconds=3600,
        )

        assert await nagger.tick_once(now=now) == 1
        poster.assert_awaited_once()

    async def test_supports_async_provider(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        """``ToolRegistry.list_tool_names`` is sync today, but the
        contract accepts async providers so a Phase 2 multi-user
        registry can fetch per-tenant lists asynchronously."""
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(hours=2),
        )

        async def provider():
            return ["fetch_url"]

        poster = AsyncMock(return_value=True)
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=provider,
            ping_poster=poster,
        )

        assert await nagger.tick_once(now=now) == 0
        poster.assert_not_awaited()

    async def test_poster_failure_does_not_stamp_last_pinged_at(
        self,
        conn: aiosqlite.Connection,
        repo: ToolRequestRepository,
    ) -> None:
        """A poster that raises must NOT stamp ``last_pinged_at`` —
        otherwise the next tick would falsely believe the nag landed
        and the user would never see the warning."""
        now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
        rid = await _seed_completed(
            conn,
            tool_name="fetch_url",
            resolved_at=now - timedelta(hours=2),
        )
        poster = AsyncMock(side_effect=RuntimeError("discord 5xx"))
        nagger = RequiresRebuildNagger(
            repository=repo,
            registered_tools_provider=lambda: [],
            ping_poster=poster,
        )
        assert await nagger.tick_once(now=now) == 0
        cur = await conn.execute(
            "SELECT last_pinged_at FROM tool_request WHERE id = ?", (rid,)
        )
        assert (await cur.fetchone())[0] is None


class TestProductionWiringContract:
    """Slice 24 review-fix — guard the boot integration so the nagger
    isn't accidentally dropped in a future cli_wiring refactor.

    These tests assert the contract the
    :func:`donna.cli_wiring.build_startup_context` boot path relies
    on (DEFAULT_TOOL_REGISTRY.list_tool_names is callable, the bot
    sends to a channel, the StartupContext slot exists). They do NOT
    boot the orchestrator — that's pytest-too-heavy. The combination
    of (a) this contract test + (b) the import from cli_wiring.py
    catches the original "exists as dead code" gap the slice-24 self-
    review surfaced.
    """

    def test_default_tool_registry_provides_list_tool_names(self) -> None:
        from donna.skills.tools import DEFAULT_TOOL_REGISTRY

        # The provider closure passes ``DEFAULT_TOOL_REGISTRY.list_tool_names``
        # directly. Confirm it returns an Iterable[str] without args
        # so the nagger's ``_live_tools`` helper works.
        names = DEFAULT_TOOL_REGISTRY.list_tool_names()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_startup_context_has_requires_rebuild_nagger_slot(self) -> None:
        from donna.cli_wiring import StartupContext

        # Assert the dataclass field exists. A future refactor that
        # drops the slot would leave the boot path orphaned (the
        # nagger gets constructed but never reachable through ctx).
        fields = {f.name for f in StartupContext.__dataclass_fields__.values()}
        assert "requires_rebuild_nagger" in fields

    def test_cli_wiring_module_imports_nagger(self) -> None:
        """The boot path imports the nagger lazily inside the wiring
        block. Asserting the source string keeps the contract
        explicit: removing the wiring block would fail this test
        (rather than only failing at production runtime when no
        Discord channel sees the reminder)."""
        from pathlib import Path

        wiring_src = (
            Path(__file__).resolve().parents[2] / "src/donna/cli_wiring.py"
        ).read_text(encoding="utf-8")
        assert "from donna.cost.requires_rebuild_nag import RequiresRebuildNagger" in wiring_src
        assert "ctx.requires_rebuild_nagger = nagger" in wiring_src
        assert "requires_rebuild_nag_loop" in wiring_src
