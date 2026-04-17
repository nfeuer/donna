from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest

from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence, MatchResult
from donna.capabilities.models import CapabilityRow


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
