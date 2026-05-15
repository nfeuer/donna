"""Verify OllamaProvider rejects tool_use and multi-turn messages."""

import pytest

from donna.models.providers.ollama import OllamaProvider


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider(base_url="http://localhost:11434")


@pytest.mark.asyncio
async def test_tools_raises(provider: OllamaProvider) -> None:
    with pytest.raises(NotImplementedError, match="tool_use"):
        await provider.complete(
            prompt="test", model="test", tools=[{"name": "web_fetch"}],
        )


@pytest.mark.asyncio
async def test_messages_raises(provider: OllamaProvider) -> None:
    with pytest.raises(NotImplementedError, match="multi-turn"):
        await provider.complete(
            prompt="test", model="test",
            messages=[{"role": "user", "content": "hi"}],
        )
