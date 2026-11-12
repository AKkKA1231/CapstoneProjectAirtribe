"""Cache-hit orchestration tests: no provider call and exact reservation release."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from prism.adapters.embeddings.local import DeterministicHashEmbedding
from prism.application.cache import SemanticCacheService
from prism.application.chat import ChatService
from prism.domain.accounting import Admission, Pricing, Settlement
from prism.domain.cache import CacheHit
from prism.domain.providers import (
    ChatCompletionRequest,
    ChatMessage,
    ProviderTarget,
    TokenUsage,
)
from prism.domain.security import TenantContext


class HitRepository:
    def __init__(self, hit: CacheHit) -> None:
        self.hit = hit
        self.marked: list[dict[str, object]] = []

    async def lookup(self, **_: object) -> CacheHit:
        return self.hit

    async def store(self, **_: object) -> None:
        raise AssertionError("a cache hit must not be stored again")

    async def mark_request_hit(self, **values: object) -> None:
        self.marked.append(values)


class Accounting:
    def __init__(self) -> None:
        self.admission = Admission(uuid4(), uuid4(), Decimal("0.25"))
        self.releases: list[tuple[str, str, str | None]] = []

    async def admit(self, **_: object) -> Admission:
        return self.admission

    async def release(
        self,
        reservation_id: object,
        now: datetime,
        reason: str,
        request_status: str = "cancelled",
        error_code: str | None = "reservation_released",
    ) -> Settlement:
        self.releases.append((reason, request_status, error_code))
        return Settlement(self.admission.reservation_id, "cancelled", Decimal(0), True)

    async def settle(self, *_: object, **__: object) -> Settlement:
        raise AssertionError("cache hits release; they do not settle provider spend")


class Providers:
    def __init__(self) -> None:
        self.target = ProviderTarget(
            uuid4(),
            "mock-a",
            "https://mock.test/v1",
            None,
            1000,
            "mock-fast-v1",
            Pricing(Decimal("0.001"), Decimal("0.002")),
        )
        self.routing_updates: list[tuple[str, object]] = []

    async def resolve_targets(self, alias: str, effective_at: datetime) -> list[ProviderTarget]:
        return [self.target]

    async def update_routing_decision(
        self, request_log_id: object, resolved_alias: str, decision: object
    ) -> None:
        self.routing_updates.append((resolved_alias, decision))

    async def update_request_execution(self, **_: object) -> None:
        raise AssertionError("cache hit must not be recorded as provider execution")

    async def record_attempt(self, *_: object, **__: object) -> None:
        raise AssertionError("cache hit must not create a provider attempt")


class NeverCoordinator:
    async def complete(self, *_: object, **__: object) -> Any:
        raise AssertionError("provider must not run on a cache hit")

    async def prepare_stream(self, *_: object, **__: object) -> Any:
        raise AssertionError("provider stream must not run on a cache hit")


def request(*, stream: bool) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="Explain caching")],
        stream=stream,
        temperature=0,
    )


def build() -> tuple[ChatService, Accounting, HitRepository, TenantContext]:
    hit = CacheHit(
        uuid4(),
        {"id": "chatcmpl-cached", "model": "fast", "choices": [{"message": {"content": "hit"}}]},
        "fast",
        "mock-a",
        TokenUsage(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        Decimal("0.001"),
        1.0,
    )
    cache_repository = HitRepository(hit)
    accounting = Accounting()
    cache = SemanticCacheService(
        cache_repository,
        DeterministicHashEmbedding(),
        enabled=True,
        ttl_seconds=3600,
        similarity_threshold=0.82,
        schema_version=1,
        maximum_temperature=0.2,
    )
    chat = ChatService(
        accounting,  # type: ignore[arg-type]
        Providers(),  # type: ignore[arg-type]
        NeverCoordinator(),  # type: ignore[arg-type]
        max_retries=1,
        cache=cache,
    )
    tenant = TenantContext(uuid4(), "Cache tenant", 60, Decimal("10"), frozenset({"fast"}))
    return chat, accounting, cache_repository, tenant


@pytest.mark.asyncio
async def test_json_cache_hit_skips_provider_and_releases_reservation_as_success() -> None:
    chat, accounting, repository, tenant = build()

    result = await chat.complete(tenant, request(stream=False))

    assert result.cache_hit is True
    assert result.provider == "cache"
    assert result.cost_usd == Decimal(0)
    assert accounting.releases == [("semantic cache hit", "success", None)]
    assert repository.marked[0]["streamed"] is False


@pytest.mark.asyncio
async def test_stream_cache_hit_preserves_sse_transport_and_done() -> None:
    chat, accounting, repository, tenant = build()

    result = await chat.stream(tenant, request(stream=True))
    chunks = [chunk async for chunk in result.events]

    assert result.cache_hit is True
    assert result.provider == "cache"
    assert result.reserved_usd == Decimal(0)
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert accounting.releases == [("semantic cache hit", "success", None)]
    assert repository.marked[0]["streamed"] is True
