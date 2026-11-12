"""Real pgvector checks for cache similarity, isolation, counters, and TTL."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from prism.adapters.database.cache import PostgresCacheRepository
from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import AdmissionCounter, CacheEntry, VirtualKey
from prism.adapters.embeddings.local import DeterministicHashEmbedding
from prism.application.cache import SemanticCacheService
from prism.domain.providers import (
    ChatCompletionRequest,
    ChatMessage,
    ProviderResponse,
    TokenUsage,
)
from prism.domain.security import TenantContext, digest_virtual_key
from prism.infrastructure.config import Settings

pytestmark = pytest.mark.skipif(
    "PRISM_TEST_DATABASE_URL" not in os.environ,
    reason="PRISM_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
async def database() -> DatabaseManager:
    manager = DatabaseManager(
        Settings(
            environment="test",
            database_url=os.environ.get(
                "PRISM_TEST_DATABASE_URL",
                "postgresql+asyncpg://prism:prism@localhost:5432/prism",
            ),
        )
    )
    yield manager
    await manager.close()


async def create_tenant(database: DatabaseManager) -> TenantContext:
    key_id = uuid4()
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(
            VirtualKey(
                id=key_id,
                key_digest=digest_virtual_key(str(key_id), "integration-pepper"),
                key_prefix=str(key_id)[:12],
                tenant_name=f"Cache integration {key_id}",
                rpm_limit=100,
                monthly_budget_usd=Decimal("10"),
                is_active=True,
            )
        )
        await session.flush()
        session.add(
            AdmissionCounter(
                key_id=key_id,
                rpm_window_start=now,
                rpm_count=0,
                budget_month=now.strftime("%Y-%m"),
                spent_usd=Decimal(0),
                reserved_usd=Decimal(0),
            )
        )
    return TenantContext(key_id, f"Cache integration {key_id}", 100, Decimal("10"), frozenset())


async def delete_tenant(database: DatabaseManager, key_id: UUID) -> None:
    async with database.session() as session, session.begin():
        await session.execute(delete(VirtualKey).where(VirtualKey.id == key_id))


def request(prompt: str, *, system: str = "Be concise") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="auto",
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=prompt),
        ],
        temperature=0,
        max_tokens=80,
        seed=42,
    )


def cache_service(database: DatabaseManager) -> SemanticCacheService:
    return SemanticCacheService(
        PostgresCacheRepository(database),
        DeterministicHashEmbedding(),
        enabled=True,
        ttl_seconds=3600,
        similarity_threshold=0.82,
        schema_version=1,
        maximum_temperature=0.2,
    )


@pytest.mark.asyncio
async def test_pgvector_cache_similarity_isolation_hit_counter_and_ttl(
    database: DatabaseManager,
) -> None:
    first = await create_tenant(database)
    second = await create_tenant(database)
    cache = cache_service(database)
    now = datetime.now(UTC)
    source = request("Explain semantic caching for an automobile")
    response = ProviderResponse(
        body={
            "id": "chatcmpl-cache-integration",
            "choices": [{"message": {"role": "assistant", "content": "Cached answer"}}],
        },
        usage=TokenUsage(prompt_tokens=8, completion_tokens=3, total_tokens=11),
    )

    try:
        await cache.store(
            first,
            source,
            "smart",
            response,
            "mock-b",
            Decimal("0.000014"),
            now,
        )

        exact, _ = await cache.lookup(first, source, "smart", now)
        paraphrase, _ = await cache.lookup(
            first, request("Describe semantic cache for a car"), "smart", now
        )
        negative, _ = await cache.lookup(
            first, request("Explain how to bake a chocolate cake"), "smart", now
        )
        cross_tenant, _ = await cache.lookup(second, source, "smart", now)
        changed_context, _ = await cache.lookup(
            first, request(source.messages[-1].content, system="Be detailed"), "smart", now
        )

        assert exact is not None and exact.similarity == pytest.approx(1.0)
        assert paraphrase is not None and paraphrase.similarity >= 0.82
        assert negative is None
        assert cross_tenant is None
        assert changed_context is None

        async with database.session() as session:
            entry = await session.scalar(
                select(CacheEntry).where(CacheEntry.key_id == first.key_id)
            )
            assert entry is not None
            assert entry.hit_count == 2

        async with database.session() as session, session.begin():
            await session.execute(
                update(CacheEntry)
                .where(CacheEntry.key_id == first.key_id)
                .values(expires_at=now - timedelta(seconds=1))
            )
        expired, _ = await cache.lookup(first, source, "smart", now)
        assert expired is None
    finally:
        await delete_tenant(database, first.key_id)
        await delete_tenant(database, second.key_id)
