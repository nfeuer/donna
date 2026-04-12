"""LLM Gateway routes — expose local Ollama to other homelab services.

Requests are enqueued into the priority queue system. Donna's internal
tasks always take priority. External requests are rate-limited and
budget-checked. See docs/superpowers/specs/2026-04-11-llm-gateway-queue-design.md.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from donna.llm.queue import QueueFullError

logger = structlog.get_logger()

router = APIRouter()


def _require_api_key(
    request: Request,
    x_api_key: str | None = Header(None),
) -> None:
    """Validate API key from gateway config."""
    config = getattr(request.app.state, "llm_gateway_config", None)
    api_key = config.api_key if config else ""
    if not api_key:
        return
    if x_api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class CompletionRequest(BaseModel):
    prompt: str
    model: str | None = Field(default=None, description="Ollama model tag.")
    max_tokens: int = Field(default=1024, ge=1, le=8192)
    json_mode: bool = Field(default=True, description="Request JSON output.")
    caller: str | None = Field(default=None, description="Calling service identifier.")
    allow_cloud: bool = Field(default=False, description="Allow Claude fallback.")


class CompletionResponse(BaseModel):
    output: Any
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


def _resolve_model(request: Request, requested: str | None) -> str:
    """Resolve model from request or gateway config default."""
    if requested:
        return requested
    config = getattr(request.app.state, "llm_gateway_config", None)
    if config:
        models_cfg = getattr(request.app.state, "models_config", None) or {}
        default_alias = "local_parser"
        model_entry = models_cfg.get("models", {}).get(default_alias, {})
        return model_entry.get("model", "qwen2.5:32b-instruct-q6_K")
    return "qwen2.5:32b-instruct-q6_K"


@router.get("/health")
async def llm_health(request: Request) -> dict[str, Any]:
    """Check if the Ollama backend is reachable."""
    ollama = getattr(request.app.state, "ollama", None)
    if ollama is None:
        return {"ok": False, "detail": "Ollama provider not initialised"}
    ok = await ollama.health()
    return {"ok": ok}


@router.get("/models")
async def llm_models(request: Request) -> dict[str, Any]:
    """List locally available models."""
    ollama = getattr(request.app.state, "ollama", None)
    if ollama is None:
        return {"models": [], "detail": "Ollama provider not initialised"}
    models = await ollama.list_models()
    return {"models": models}


@router.get("/queue/status")
async def llm_queue_status(request: Request) -> dict[str, Any]:
    """Live queue status for the dashboard."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        return {"error": "Queue worker not initialised"}
    return queue.get_status()


@router.get("/queue/item/{sequence}")
async def llm_queue_item(sequence: int, request: Request) -> dict[str, Any]:
    """Return full details for a single queued or in-progress item."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")
    item = queue.get_item(sequence)
    if item is None:
        raise HTTPException(404, "Item not found in queue")
    return item


@router.get("/queue/stream")
async def llm_queue_stream(request: Request) -> StreamingResponse:
    """SSE stream of queue state changes."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")

    async def event_generator():
        try:
            # Send initial state immediately
            status = queue.get_status()
            yield f"data: {json.dumps(status)}\n\n"

            while True:
                try:
                    async with asyncio.timeout(15):
                        async with queue.state_changed:
                            await queue.state_changed.wait()
                    status = queue.get_status()
                    yield f"data: {json.dumps(status)}\n\n"
                except TimeoutError:
                    # Heartbeat
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/completions", dependencies=[Depends(_require_api_key)])
async def llm_completion(
    body: CompletionRequest,
    request: Request,
) -> CompletionResponse:
    """Enqueue a completion request. Blocks until result is ready."""
    queue = getattr(request.app.state, "llm_queue", None)
    if queue is None:
        raise HTTPException(503, "Queue worker not initialised")

    # Rate limit check
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    if rate_limiter and body.caller and not rate_limiter.check(body.caller):
        config = request.app.state.llm_gateway_config
        alerter = getattr(request.app.state, "gateway_alerter", None)
        if alerter:
            rejections = rate_limiter.recent_rejections(body.caller)
            if rejections >= config.rate_limit_alert_threshold:
                usage = rate_limiter.get_usage(body.caller)
                await alerter.alert_rate_limited(
                    body.caller, usage["minute_count"], usage["minute_limit"]
                )
        raise HTTPException(
            429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    model = _resolve_model(request, body.model)

    try:
        future = await queue.enqueue_external(
            prompt=body.prompt,
            model=model,
            max_tokens=body.max_tokens,
            json_mode=body.json_mode,
            caller=body.caller,
            allow_cloud=body.allow_cloud,
        )
    except QueueFullError as exc:
        raise HTTPException(
            503,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc

    try:
        result, meta = await future
    except asyncio.CancelledError as exc:
        raise HTTPException(504, "Request was preempted and not completed") from exc
    except Exception as exc:
        raise HTTPException(502, f"LLM error: {exc}") from exc

    return CompletionResponse(
        output=result,
        model=model,
        tokens_in=meta.tokens_in,
        tokens_out=meta.tokens_out,
        latency_ms=meta.latency_ms,
    )
