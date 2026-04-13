"""Tests that OllamaProvider forwards num_ctx into the request payload."""

from __future__ import annotations

from typing import Any

import pytest

from donna.models.providers.ollama import OllamaProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status = 200

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return {
            "message": {"content": '{"ok": true}'},
            "model": "qwen2.5:32b-instruct-q6_K",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }


class _FakeSession:
    def __init__(self) -> None:
        self.last_post_json: dict[str, Any] | None = None
        self.closed = False

    def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.last_post_json = json
        return _FakeResponse(json)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_num_ctx_is_sent_in_options() -> None:
    provider = OllamaProvider()
    fake = _FakeSession()
    provider._session = fake  # type: ignore[assignment]

    await provider.complete(
        prompt="hello",
        model="qwen2.5:32b-instruct-q6_K",
        max_tokens=512,
        num_ctx=8192,
    )

    assert fake.last_post_json is not None
    assert fake.last_post_json["options"]["num_ctx"] == 8192
    assert fake.last_post_json["options"]["num_predict"] == 512


@pytest.mark.asyncio
async def test_num_ctx_defaults_when_not_provided() -> None:
    provider = OllamaProvider()
    fake = _FakeSession()
    provider._session = fake  # type: ignore[assignment]

    await provider.complete(prompt="hello", model="qwen2.5:32b-instruct-q6_K")

    assert fake.last_post_json is not None
    # When the caller does not pass num_ctx, we must still send one so
    # Ollama does not fall back to its 2048 default. Default matches the
    # OllamaConfig.default_num_ctx starting value.
    assert fake.last_post_json["options"]["num_ctx"] == 8192
