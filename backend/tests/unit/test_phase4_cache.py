"""Phase 4 semantic-cache scoping, threshold, and transport checks."""

import asyncio
import json
import math
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from prism.adapters.embeddings.local import DeterministicHashEmbedding
from prism.application.cache import SemanticCacheService
from prism.domain.cache import CacheHit
from prism.domain.providers import (
    ChatCompletionRequest,
    ChatMessage,
    ProviderResponse,
    TokenUsage,
)


class RecordingCacheRepository:
    def __init__(self) -> None:
        self.stores: list[dict[str, Any]] = []

    async def lookup(self, **_: object) -> CacheHit | None:
        return None

    async def store(self, **values: Any) -> None:
        self.stores.append(values)

    async def mark_request_hit(self, **_: object) -> None:
        return None


def service(repository: RecordingCacheRepository | None = None) -> SemanticCacheService:
    return SemanticCacheService(
        repository or RecordingCacheRepository(),
        DeterministicHashEmbedding(),
        enabled=True,
        ttl_seconds=3600,
        similarity_threshold=0.82,
        schema_version=1,
        maximum_temperature=0.2,
    )


def request(
    prompt: str,
    *,
    stream: bool = False,
    temperature: float = 0.0,
    system: str = "Be concise",
    max_tokens: int = 100,
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="auto",
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=prompt),
        ],
        stream=stream,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=7,
    )


def test_hash_embedding_is_deterministic_normalized_and_threshold_separates_cases() -> None:
    embedder = DeterministicHashEmbedding()
    source = embedder.embed("Explain semantic caching for an automobile")
    paraphrase = embedder.embed("Describe semantic cache for a car")
    negative = embedder.embed("Explain how to bake a chocolate cake")

    def similarity(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right, strict=True))

    assert len(source) == 1536
    assert math.sqrt(sum(value * value for value in source)) == pytest.approx(1.0)
    assert source == embedder.embed("Explain semantic caching for an automobile")
    assert similarity(source, paraphrase) >= 0.82
    assert similarity(source, negative) < 0.82


def test_scope_includes_context_options_and_resolved_tier_but_not_transport() -> None:
    cache = service()
    base = cache.query(request("Explain caching"), "smart")
    paraphrase = cache.query(request("Describe cache"), "smart")

    assert base.scope_hash == paraphrase.scope_hash
    assert base.request_fingerprint != paraphrase.request_fingerprint
    assert (
        cache.query(request("Explain caching", stream=True), "smart").scope_hash == base.scope_hash
    )
    assert (
        cache.query(request("Explain caching", system="Be detailed"), "smart").scope_hash
        != base.scope_hash
    )
    assert (
        cache.query(request("Explain caching", max_tokens=101), "smart").scope_hash
        != base.scope_hash
    )
    assert cache.query(request("Explain caching"), "fast").scope_hash != base.scope_hash


def test_eligibility_and_dimension_validation() -> None:
    assert service().eligible(request("hello", temperature=0.2))
    assert not service().eligible(request("hello", temperature=0.3))

    class WrongDimensionEmbedding:
        model = "wrong"
        version = "v1"
        dimensions = 10

        def embed(self, text: str) -> list[float]:
            return [0.0] * 10

    with pytest.raises(ValueError, match="1536"):
        SemanticCacheService(
            RecordingCacheRepository(),
            WrongDimensionEmbedding(),
            enabled=True,
            ttl_seconds=60,
            similarity_threshold=0.8,
            schema_version=1,
            maximum_temperature=0.2,
        )


def test_tool_call_responses_are_not_cacheable() -> None:
    cache = service()
    response = ProviderResponse(
        body={
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {"tool_calls": [{"id": "call-1"}]},
                }
            ]
        },
        usage=TokenUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )

    assert cache._response_cacheable(response) is False


def test_cached_response_converts_to_valid_sse_and_done_marker() -> None:
    hit = CacheHit(
        uuid4(),
        {"id": "chatcmpl-original", "choices": [{"message": {"content": "cached text"}}]},
        "smart",
        "mock-b",
        TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        Decimal("0.001"),
        0.95,
    )

    async def collect() -> list[bytes]:
        return [chunk async for chunk in service().cached_sse(hit, "auto")]

    chunks = asyncio.run(collect())

    assert chunks[-1] == b"data: [DONE]\n\n"
    payloads = [json.loads(chunk.removeprefix(b"data: ").strip()) for chunk in chunks[:-1]]
    assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert payloads[1]["choices"][0]["delta"] == {"content": "cached text"}
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(payload["model"] == "auto" for payload in payloads)
