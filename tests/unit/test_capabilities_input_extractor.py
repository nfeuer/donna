from unittest.mock import AsyncMock, MagicMock

from donna.capabilities.input_extractor import LocalLLMInputExtractor


async def test_extractor_returns_llm_output():
    router = AsyncMock()
    router.complete.return_value = (
        {"raw_text": "draft the review", "user_id": "nick"},
        MagicMock(invocation_id="inv-1"),
    )
    extractor = LocalLLMInputExtractor(router)
    result = await extractor.extract(
        user_message="draft the review",
        schema={"type": "object", "properties": {"raw_text": {"type": "string"}, "user_id": {"type": "string"}}, "required": ["raw_text", "user_id"]},
        user_id="nick",
    )
    assert result == {"raw_text": "draft the review", "user_id": "nick"}


async def test_extractor_returns_empty_dict_on_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = Exception("model_unavailable")
    extractor = LocalLLMInputExtractor(router)
    result = await extractor.extract(
        user_message="anything",
        schema={"type": "object", "properties": {}, "required": []},
        user_id="nick",
    )
    assert result == {}


async def test_extractor_prompt_includes_schema_field_names():
    router = AsyncMock()
    router.complete.return_value = ({"url": "x"}, MagicMock(invocation_id="i"))
    extractor = LocalLLMInputExtractor(router)
    await extractor.extract(
        user_message="msg",
        schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "product URL"},
                "price_threshold_usd": {"type": "number", "description": "alert below"},
            },
            "required": ["url"],
        },
        user_id="nick",
    )
    call_args = router.complete.call_args
    prompt = call_args.kwargs.get("prompt", call_args.args[0] if call_args.args else "")
    assert "url" in prompt
    assert "price_threshold_usd" in prompt
    assert "product URL" in prompt
