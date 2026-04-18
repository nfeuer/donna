from unittest.mock import AsyncMock
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence, MatchResult
from donna.capabilities.models import CapabilityRow
from donna.capabilities.registry import CapabilityRegistry, CapabilityInput


def _cap(name: str) -> CapabilityRow:
    return CapabilityRow(
        id="id-" + name, name=name, description="desc " + name,
        input_schema={}, trigger_type="on_message", default_output_shape=None,
        status="active", embedding=None,
        created_at=datetime.now(timezone.utc), created_by="seed", notes=None,
    )


async def test_high_confidence_match():
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("product_watch"), 0.92)]
    matcher = CapabilityMatcher(registry)
    result = await matcher.match("monitor this shirt for sales")
    assert result.confidence == MatchConfidence.HIGH
    assert result.best_match.name == "product_watch"


async def test_medium_confidence_match():
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("news_check"), 0.55), (_cap("product_watch"), 0.40)]
    matcher = CapabilityMatcher(registry)
    result = await matcher.match("keep tabs on current events")
    assert result.confidence == MatchConfidence.MEDIUM
    assert result.best_match.name == "news_check"


async def test_low_confidence_match():
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("irrelevant"), 0.2)]
    matcher = CapabilityMatcher(registry)
    result = await matcher.match("do something completely novel")
    assert result.confidence == MatchConfidence.LOW
    assert result.best_match is None


async def test_no_matches_returned():
    registry = AsyncMock()
    registry.semantic_search.return_value = []
    matcher = CapabilityMatcher(registry)
    result = await matcher.match("anything")
    assert result.confidence == MatchConfidence.LOW
    assert result.best_match is None


async def test_match_result_exposes_candidates():
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("a"), 0.8), (_cap("b"), 0.6)]
    matcher = CapabilityMatcher(registry)
    result = await matcher.match("query")
    assert len(result.candidates) == 2
    assert result.candidates[0][1] == 0.8


async def test_list_all_passes_through_to_registry_mock():
    """Mocked test: list_all forwards to registry.list_all with status filter."""
    registry = AsyncMock()
    registry.list_all.return_value = [_cap("a"), _cap("b")]
    matcher = CapabilityMatcher(registry)

    rows = await matcher.list_all()

    assert [r.name for r in rows] == ["a", "b"]
    registry.list_all.assert_awaited_once_with(status="active")


async def test_list_all_honors_status_override():
    registry = AsyncMock()
    registry.list_all.return_value = []
    matcher = CapabilityMatcher(registry)

    await matcher.list_all(status="pending_review")

    registry.list_all.assert_awaited_once_with(status="pending_review")


@pytest.fixture
async def real_registry(tmp_path: Path):
    """Real CapabilityRegistry backed by an in-memory SQLite schema."""
    db_path = tmp_path / "matcher_list_all.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            embedding BLOB,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            notes TEXT
        );
        CREATE INDEX ix_capability_status ON capability(status);
        CREATE INDEX ix_capability_trigger_type ON capability(trigger_type);
    """)
    await conn.commit()
    yield CapabilityRegistry(conn)
    await conn.close()


async def test_list_all_against_real_registry(real_registry):
    """Non-mocked: exercise matcher.list_all end-to-end against a real
    CapabilityRegistry. Guards against the signature drift that caused
    _snapshot_capabilities to silently return [] in production.
    """
    for name in ("product_watch", "news_check"):
        await real_registry.register(
            CapabilityInput(
                name=name,
                description=f"desc for {name}",
                input_schema={"type": "object", "properties": {}},
                trigger_type="on_message",
            ),
            created_by="seed",
        )

    matcher = CapabilityMatcher(real_registry)

    rows = await matcher.list_all()

    assert {r.name for r in rows} == {"product_watch", "news_check"}
    assert all(r.status == "active" for r in rows)
