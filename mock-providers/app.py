"""Deterministic OpenAI-compatible provider used for local contract testing."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from time import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI(title="Prism Mock Provider", docs_url=None, redoc_url=None)
PROVIDER_NAME = os.getenv("MOCK_PROVIDER_NAME", "mock-provider")


def _prompt(body: dict[str, object]) -> str:
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return ""
    return " ".join(
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict)
    )


def _usage(prompt: str, completion: str) -> dict[str, int]:
    prompt_tokens = max(1, len(prompt.split()))
    completion_tokens = max(1, len(completion.split()))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _failure(prompt: str) -> Response | None:
    primary_only = PROVIDER_NAME == "mock-a"
    if "[[503]]" in prompt and primary_only:
        return JSONResponse({"error": {"message": "planned 503"}}, status_code=503)
    if "[[429]]" in prompt and primary_only:
        return JSONResponse({"error": {"message": "planned 429"}}, status_code=429)
    if "[[fatal]]" in prompt:
        return JSONResponse(
            {"error": {"message": "planned fatal error"}}, status_code=400
        )
    if "[[malformed]]" in prompt and primary_only:
        return Response("not-json", media_type="application/json")
    return None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": PROVIDER_NAME}


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    prompt = _prompt(body)
    failure = _failure(prompt)
    if failure is not None:
        return failure

    model = str(body.get("model", "mock-model"))
    response_id = f"chatcmpl-{uuid4().hex}"
    completion = f"{PROVIDER_NAME} response: {prompt}"
    if not body.get("stream", False):
        return JSONResponse(
            {
                "id": response_id,
                "object": "chat.completion",
                "created": int(time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": completion},
                        "finish_reason": "stop",
                    }
                ],
                "usage": _usage(prompt, completion),
            }
        )

    async def event_stream() -> AsyncIterator[bytes]:
        role_chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time()),
            "model": model,
            "choices": [
                {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
            ],
        }
        yield f"data: {json.dumps(role_chunk)}\n\n".encode()
        for index, token in enumerate(completion.split()):
            chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": token + " "},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n".encode()
            await asyncio.sleep(0.01)
            if "[[stream-fail-after-first]]" in prompt and index == 0:
                return
        final_chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": _usage(prompt, completion),
        }
        yield f"data: {json.dumps(final_chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
