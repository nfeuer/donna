"""Wave 3 extensions to ChallengerMatchResult shape."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from donna.agents.challenger_agent import ChallengerAgent, ChallengerMatchResult
from donna.capabilities.matcher import MatchConfidence, MatchResult
from donna.capabilities.models import CapabilityRow


def test_result_has_intent_kind_field() -> None:
    r = ChallengerMatchResult(status="ready", intent_kind="automation")
    assert r.intent_kind == "automation"


def test_result_defaults() -> None:
    r = ChallengerMatchResult(status="ready")
    assert r.intent_kind == "task"
    assert r.schedule is None
    assert r.deadline is None
    assert r.alert_conditions is None
    assert r.confidence == 0.0
    assert r.low_quality_signals == []


def test_result_with_automation_fields() -> None:
    r = ChallengerMatchResult(
        status="ready",
        intent_kind="automation",
        schedule={"cron": "0 12 * * *", "human_readable": "daily at noon"},
        alert_conditions={"expression": "price < 100", "channels": ["discord_dm"]},
        confidence=0.92,
        low_quality_signals=[],
    )
    assert r.schedule["cron"] == "0 12 * * *"
    assert r.alert_conditions["expression"] == "price < 100"


def test_result_with_task_fields() -> None:
    deadline = datetime(2026, 4, 24, tzinfo=timezone.utc)
    r = ChallengerMatchResult(status="ready", intent_kind="task", deadline=deadline)
    assert r.deadline == deadline


# ---------------------------------------------------------------------------
# LLM-integration tests for the unified parse path
# ---------------------------------------------------------------------------


def _product_watch_cap() -> CapabilityRow:
    return CapabilityRow(
        id="cap-1",
        name="product_watch",
        description="Watch a product URL for price/availability",
        input_schema={
            "required": ["url", "required_size", "max_price_usd"],
            "properties": {
                "url": {"type": "string"},
                "required_size": {"type": "string"},
                "max_price_usd": {"type": "number"},
            },
        },
        trigger_type="on_schedule",
        default_output_shape=None,
        status="active",
        embedding=None,
        created_at=datetime.now(timezone.utc),
        created_by="seed",
        notes=None,
    )


class _FakeRouter:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(self, prompt, *, task_type, user_id, schema=None, model_alias=None, **kwargs):
        self.calls.append((task_type, user_id))
        return self._response, {"cost_usd": 0.0, "latency_ms": 50}


class _FakeMatcher:
    def __init__(self) -> None:
        self._cap = _product_watch_cap()

    async def match(self, message: str) -> MatchResult:
        return MatchResult(
            confidence=MatchConfidence.HIGH,
            best_match=self._cap,
            best_score=0.9,
            candidates=[(self._cap, 0.9)],
        )

    async def list_all(self) -> list[CapabilityRow]:
        return [self._cap]


@pytest.mark.asyncio
async def test_match_and_extract_returns_automation_result_from_llm() -> None:
    router_response = {
        "intent_kind": "automation",
        "capability_name": "product_watch",
        "match_score": 0.9,
        "confidence": 0.92,
        "extracted_inputs": {
            "url": "https://x.com/shirt",
            "required_size": "L",
            "max_price_usd": 100,
        },
        "schedule": {"cron": "0 12 * * *", "human_readable": "daily at noon"},
        "deadline": None,
        "alert_conditions": {
            "expression": "triggers_alert == true",
            "channels": ["discord_dm"],
        },
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }
    router = _FakeRouter(router_response)
    agent = ChallengerAgent(
        matcher=_FakeMatcher(), input_extractor=None, model_router=router
    )
    result = await agent.match_and_extract(
        "watch https://x.com/shirt daily for size L under $100", "u1"
    )
    assert result.status == "ready"
    assert result.intent_kind == "automation"
    assert result.schedule["cron"] == "0 12 * * *"
    assert result.alert_conditions["expression"] == "triggers_alert == true"
    assert result.confidence == pytest.approx(0.92)
    assert result.capability is not None
    assert result.capability.name == "product_watch"
    assert router.calls == [("challenge_task", "u1")]


@pytest.mark.asyncio
async def test_match_and_extract_escalates_when_llm_hallucinates_capability() -> None:
    """LLM returns a capability name that isn't in the snapshot → force
    escalate_to_claude and leave capability=None. Previously the status
    ladder would set status=ready with capability=None, which downstream
    consumers cannot act on safely.
    """
    router_response = {
        "intent_kind": "task",
        "capability_name": "totally_made_up_capability",
        "match_score": 0.88,
        "confidence": 0.91,
        "extracted_inputs": {},
        "schedule": None,
        "deadline": None,
        "alert_conditions": None,
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }
    router = _FakeRouter(router_response)
    agent = ChallengerAgent(
        matcher=_FakeMatcher(), input_extractor=None, model_router=router
    )

    result = await agent.match_and_extract("do the made-up thing", "u1")

    assert result.status == "escalate_to_claude"
    assert result.capability is None


@pytest.mark.asyncio
async def test_match_and_extract_needs_input_when_missing_fields() -> None:
    router_response = {
        "intent_kind": "automation",
        "capability_name": "product_watch",
        "match_score": 0.85,
        "confidence": 0.7,
        "extracted_inputs": {},
        "schedule": None,
        "deadline": None,
        "alert_conditions": None,
        "missing_fields": ["url", "max_price_usd", "required_size"],
        "clarifying_question": "Which URL, what size, and what's the max price?",
        "low_quality_signals": [],
    }
    router = _FakeRouter(router_response)
    agent = ChallengerAgent(
        matcher=_FakeMatcher(), input_extractor=None, model_router=router
    )
    result = await agent.match_and_extract("watch the Patagonia jacket", "u1")
    assert result.status == "needs_input"
    assert result.missing_fields == ["url", "max_price_usd", "required_size"]
    assert result.clarifying_question.startswith("Which URL")
