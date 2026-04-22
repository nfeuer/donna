from unittest.mock import AsyncMock, MagicMock

from donna.skills.triage import TriageAgent, TriageDecision, TriageInput


async def test_triage_returns_retry_decision():
    router = AsyncMock()
    router.complete.return_value = (
        {
            "decision": "retry_step_with_modified_prompt",
            "rationale": "output was close; prompt clarification should help",
            "modified_prompt_additions": "Be stricter about field types.",
        },
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="extract",
            error_type="schema_validation",
            error_message="missing required field title",
            state={"extract_attempt_1": {"confidence": 0.6}},
            skill_yaml_preview="...",
            user_id="nick",
            retry_count=0,
        ),
    )

    assert result.decision == TriageDecision.RETRY_STEP
    assert result.rationale.startswith("output was close")
    assert "stricter" in (result.modified_prompt_additions or "")


async def test_triage_returns_escalate_decision():
    router = AsyncMock()
    router.complete.return_value = (
        {
            "decision": "escalate_to_claude",
            "rationale": "tool unavailable; only Claude can proceed",
        },
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="fetch", error_type="tool_exhausted",
            error_message="web_fetch timeout x3", state={},
            skill_yaml_preview="", user_id="nick", retry_count=3,
        ),
    )

    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE


async def test_triage_respects_retry_cap():
    """Triage cannot return retry if retry_count >= MAX_RETRY_COUNT."""
    router = AsyncMock()
    router.complete.return_value = (
        {"decision": "retry_step_with_modified_prompt", "rationale": "try again"},
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="x", error_type="schema_validation",
            error_message="...", state={}, skill_yaml_preview="",
            user_id="nick", retry_count=3,  # at the cap
        ),
    )

    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE
    assert "retry cap" in result.rationale.lower()


async def test_triage_handles_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = RuntimeError("model unavailable")
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="x", error_type="any",
            error_message="...", state={}, skill_yaml_preview="",
            user_id="nick", retry_count=0,
        ),
    )

    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE
    assert "triage LLM failed" in result.rationale


async def test_triage_invalid_decision_falls_back_to_escalate():
    router = AsyncMock()
    router.complete.return_value = (
        {"decision": "nonsense_decision", "rationale": "x"},
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="x", error_type="x",
            error_message="x", state={}, skill_yaml_preview="",
            user_id="nick", retry_count=0,
        ),
    )

    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE
