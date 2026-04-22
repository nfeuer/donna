"""Wave 3 extensions to ChallengerMatchResult shape."""
from __future__ import annotations

from datetime import UTC, datetime

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
    deadline = datetime(2026, 4, 24, tzinfo=UTC)
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
        created_at=datetime.now(UTC),
        created_by="seed",
        notes=None,
    )


class _FakeRouter:
    def __init__(self, response: dict, schema: dict | None = None) -> None:
        self._response = response
        self._schema = schema or {}
        self.calls: list[tuple[str, str]] = []
        self.prompts: list[str] = []

    async def complete(
        self, prompt, *, task_type, user_id, schema=None, model_alias=None, **kwargs,
    ):
        self.calls.append((task_type, user_id))
        self.prompts.append(prompt)
        return self._response, {"cost_usd": 0.0, "latency_ms": 50}

    def get_output_schema(self, task_type: str) -> dict:
        return self._schema


class _FakeMatcher:
    def __init__(self) -> None:
        self._cap = _product_watch_cap()
        self.list_all_calls = 0

    async def match(self, message: str) -> MatchResult:
        return MatchResult(
            confidence=MatchConfidence.HIGH,
            best_match=self._cap,
            best_score=0.9,
            candidates=[(self._cap, 0.9)],
        )

    async def list_all(self) -> list[CapabilityRow]:
        self.list_all_calls += 1
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


# ---------------------------------------------------------------------------
# F-W3-H: schema validation on the LLM parse path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_and_extract_validates_schema_and_degrades() -> None:
    """F-W3-H: when LLM output fails schema validation, log and degrade
    (don't crash). The Wave 3 parse path now calls validate_output the
    same way the novelty judge and other agents do. On failure, we log
    and let _build_result_from_parse compute a best-effort result from
    whatever fields are present, rather than bubbling up a validation
    exception to the Discord handler.
    """
    # Response missing every required field (intent_kind, confidence, match_score).
    bad_response: dict = {"foo": "bar"}
    # Use the real challenger_parse schema so the required-field check fires.
    import json as _json
    import pathlib as _pl

    schema = _json.loads(_pl.Path("schemas/challenger_parse.json").read_text())
    router = _FakeRouter(bad_response, schema=schema)
    agent = ChallengerAgent(
        matcher=_FakeMatcher(), input_extractor=None, model_router=router
    )
    # Should not raise — the "log and degrade" path kicks in.
    result = await agent.match_and_extract("do something vague", "u1")
    # Defaults from _build_result_from_parse when fields are missing:
    # intent_kind defaults to "task", confidence/match_score to 0 → escalate.
    assert result.status == "escalate_to_claude"


# ---------------------------------------------------------------------------
# F-W3-K: capability snapshot TTL cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_snapshot_cache_reuses_across_calls() -> None:
    """F-W3-K: _snapshot_capabilities should hit the matcher at most once
    within the TTL window, not once per message.
    """
    router_response = {
        "intent_kind": "task",
        "capability_name": "product_watch",
        "match_score": 0.9,
        "confidence": 0.92,
        "extracted_inputs": {},
        "schedule": None,
        "deadline": None,
        "alert_conditions": None,
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }
    matcher = _FakeMatcher()
    router = _FakeRouter(router_response)
    agent = ChallengerAgent(
        matcher=matcher,
        input_extractor=None,
        model_router=router,
        capability_snapshot_ttl_s=60.0,
    )

    await agent.match_and_extract("watch something", "u1")
    await agent.match_and_extract("watch something else", "u1")
    await agent.match_and_extract("watch a third thing", "u1")

    # First call populates the cache; calls 2 and 3 hit the cache.
    assert matcher.list_all_calls == 1


@pytest.mark.asyncio
async def test_capability_snapshot_cache_expires_after_ttl() -> None:
    """F-W3-K: after TTL elapses, the snapshot is re-fetched."""
    router_response = {
        "intent_kind": "task",
        "capability_name": "product_watch",
        "match_score": 0.9,
        "confidence": 0.92,
        "extracted_inputs": {},
        "schedule": None,
        "deadline": None,
        "alert_conditions": None,
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }
    matcher = _FakeMatcher()
    router = _FakeRouter(router_response)
    # TTL of 0 seconds -> every call is a cache miss.
    agent = ChallengerAgent(
        matcher=matcher,
        input_extractor=None,
        model_router=router,
        capability_snapshot_ttl_s=0.0,
    )

    await agent.match_and_extract("watch something", "u1")
    await agent.match_and_extract("watch something else", "u1")
    assert matcher.list_all_calls == 2


@pytest.mark.asyncio
async def test_parse_prompt_current_date_iso_uses_z_suffix() -> None:
    """The rendered parse prompt must emit ``YYYY-MM-DDTHH:MM:SSZ`` for
    current_date_iso (strict ISO-8601-with-Z), not ``+00:00``. Ensures
    prompt fixtures and LLM outputs line up on the same timestamp format.
    """
    import re

    router_response = {
        "intent_kind": "task",
        "capability_name": None,
        "match_score": 0.0,
        "confidence": 0.3,
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
    await agent.match_and_extract("hello", "u1")
    assert len(router.prompts) == 1
    prompt = router.prompts[0]
    # Find a YYYY-MM-DDTHH:MM:SSZ stamp in the rendered prompt.
    assert re.search(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b",
        prompt,
    ), f"prompt missing Z-suffixed ISO date:\n{prompt}"
    # And confirm we did NOT emit the +00:00 form.
    assert "+00:00" not in prompt
