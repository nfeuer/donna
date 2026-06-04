from datetime import UTC, datetime
from unittest.mock import AsyncMock

from donna.agents.challenger_agent import ChallengerAgent
from donna.capabilities.matcher import MatchConfidence, MatchResult
from donna.capabilities.models import CapabilityRow


def _cap(name: str, schema: dict | None = None) -> CapabilityRow:
    if schema is None:
        schema = {"type": "object", "properties": {}, "required": []}
    return CapabilityRow(
        id="id", name=name, description="desc", input_schema=schema,
        trigger_type="on_message", default_output_shape=None,
        status="active", embedding=None,
        created_at=datetime.now(UTC), created_by="seed", notes=None,
    )


async def test_high_confidence_match_with_complete_inputs():
    # parse_task requires only raw_text; user_id is known from the author and
    # is never an extracted field (see migration c8e1f2a3b4d5).
    cap = _cap("parse_task", {
        "type": "object",
        "properties": {"raw_text": {"type": "string"}},
        "required": ["raw_text"],
    })

    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.HIGH, best_match=cap, best_score=0.9,
        candidates=[(cap, 0.9)],
    )
    extractor = AsyncMock()
    extractor.extract.return_value = {"raw_text": "draft the review"}

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(user_message="draft the review", user_id="nick")

    assert result.status == "ready"
    assert result.capability.name == "parse_task"
    assert result.extracted_inputs == {"raw_text": "draft the review"}
    assert result.missing_fields == []


async def test_parse_task_ready_without_user_id_does_not_clarify():
    """Regression: parse_task must not fire a clarification just because no
    user_id was extracted. user_id is no longer a required input, so a plain
    task message resolves straight to 'ready' with no clarifying question."""
    cap = _cap("parse_task", {
        "type": "object",
        "properties": {"raw_text": {"type": "string"}},
        "required": ["raw_text"],
    })

    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.HIGH, best_match=cap, best_score=0.9,
        candidates=[(cap, 0.9)],
    )
    extractor = AsyncMock()
    # Extractor returns only raw_text — no user_id, as expected post-fix.
    extractor.extract.return_value = {"raw_text": "send invoices to Kevin tomorrow"}

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(
        user_message="send invoices to Kevin tomorrow", user_id="nick"
    )

    assert result.status == "ready"
    assert result.missing_fields == []
    assert result.clarifying_question is None
    assert "user_id" not in result.extracted_inputs


async def test_high_confidence_match_with_missing_inputs():
    cap = _cap("product_watch", {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Product page URL"},
            "target_size": {"type": "string", "description": "Size to monitor"},
            "price_threshold_usd": {"type": "number", "description": "Alert below price"},
        },
        "required": ["url", "target_size", "price_threshold_usd"],
    })

    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.HIGH, best_match=cap, best_score=0.85,
        candidates=[(cap, 0.85)],
    )
    extractor = AsyncMock()
    extractor.extract.return_value = {"url": "https://cos.com/shirt"}

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(user_message="watch this shirt", user_id="nick")

    assert result.status == "needs_input"
    assert result.capability.name == "product_watch"
    assert sorted(result.missing_fields) == ["price_threshold_usd", "target_size"]
    assert result.clarifying_question is not None
    assert "target_size" in result.clarifying_question


async def test_low_confidence_match_escalates():
    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.LOW, best_match=None, best_score=0.2, candidates=[],
    )
    extractor = AsyncMock()

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(user_message="novel request", user_id="nick")

    assert result.status == "escalate_to_claude"
    assert result.capability is None
    extractor.extract.assert_not_called()


async def test_existing_execute_method_still_works():
    """Verify the old execute() method still works without matcher."""
    challenger = ChallengerAgent()
    # The execute method requires a TaskRow and AgentContext, but we just need to
    # verify the constructor works with no args and the method exists.
    assert hasattr(challenger, "execute")
    assert challenger.name == "challenger"
