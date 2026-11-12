"""Exact tenant-filtered pgvector semantic-cache repository."""

from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update

from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import CacheEntry, RequestLog
from prism.domain.cache import CacheHit, CacheQuery
from prism.domain.providers import TokenUsage


class PostgresCacheRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def lookup(
        self,
        *,
        key_id: UUID,
        query: CacheQuery,
        now: datetime,
        embedding_model: str,
        embedding_version: str,
        schema_version: int,
        similarity_threshold: float,
    ) -> CacheHit | None:
        distance = CacheEntry.prompt_embedding.cosine_distance(query.embedding).label("distance")
        async with self._database.session() as session, session.begin():
            row = (
                await session.execute(
                    select(CacheEntry, distance)
                    .where(
                        CacheEntry.key_id == key_id,
                        CacheEntry.scope_hash == query.scope_hash,
                        CacheEntry.expires_at > now,
                        CacheEntry.embedding_model == embedding_model,
                        CacheEntry.embedding_version == embedding_version,
                        CacheEntry.cache_schema_version == schema_version,
                    )
                    .order_by(distance)
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            entry = cast(CacheEntry, row[0])
            distance_value = cast(float, row[1])
            similarity = 1.0 - float(distance_value)
            required_similarity = max(similarity_threshold, entry.similarity_threshold)
            if similarity < required_similarity:
                return None
            await session.execute(
                update(CacheEntry)
                .where(CacheEntry.id == entry.id)
                .values(hit_count=CacheEntry.hit_count + 1, last_hit_at=now)
            )
            usage = None
            if entry.prompt_tokens is not None and entry.completion_tokens is not None:
                usage = TokenUsage(
                    prompt_tokens=entry.prompt_tokens,
                    completion_tokens=entry.completion_tokens,
                    total_tokens=entry.prompt_tokens + entry.completion_tokens,
                )
            return CacheHit(
                entry_id=entry.id,
                response_body=entry.response_body,
                model=entry.model,
                provider=entry.provider,
                usage=usage,
                original_cost_usd=entry.cost_usd,
                similarity=similarity,
            )

    async def store(
        self,
        *,
        key_id: UUID,
        query: CacheQuery,
        response_body: dict[str, Any],
        model: str,
        provider: str,
        usage: TokenUsage,
        cost_usd: Decimal,
        now: datetime,
        expires_at: datetime,
        embedding_model: str,
        embedding_version: str,
        schema_version: int,
        similarity_threshold: float,
    ) -> None:
        async with self._database.session() as session, session.begin():
            existing = await session.scalar(
                select(CacheEntry)
                .where(
                    CacheEntry.key_id == key_id,
                    CacheEntry.scope_hash == query.scope_hash,
                    CacheEntry.request_fingerprint == query.request_fingerprint,
                    CacheEntry.embedding_model == embedding_model,
                    CacheEntry.embedding_version == embedding_version,
                    CacheEntry.cache_schema_version == schema_version,
                )
                .order_by(CacheEntry.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            values = {
                "prompt_text": query.prompt_text,
                "prompt_embedding": query.embedding,
                "response_body": response_body,
                "model": model,
                "provider": provider,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cost_usd": cost_usd,
                "similarity_threshold": similarity_threshold,
                "expires_at": expires_at,
            }
            if existing is not None:
                for name, value in values.items():
                    setattr(existing, name, value)
                return
            session.add(
                CacheEntry(
                    key_id=key_id,
                    scope_hash=query.scope_hash,
                    request_fingerprint=query.request_fingerprint,
                    embedding_model=embedding_model,
                    embedding_version=embedding_version,
                    cache_schema_version=schema_version,
                    created_at=now,
                    **values,
                )
            )

    async def mark_request_hit(
        self,
        *,
        request_log_id: UUID,
        hit: CacheHit,
        resolved_alias: str,
        streamed: bool,
        now: datetime,
    ) -> None:
        async with self._database.session() as session, session.begin():
            request_log = await session.get(RequestLog, request_log_id)
            if request_log is None:
                raise LookupError("request log not found")
            request_log.resolved_alias = resolved_alias
            request_log.model = hit.model
            request_log.status = "success"
            request_log.cache_hit = True
            request_log.cache_entry_id = hit.entry_id
            request_log.streamed = streamed
            request_log.cost_usd = Decimal(0)
            request_log.completed_at = now
            if hit.usage is not None:
                request_log.prompt_tokens = hit.usage.prompt_tokens
                request_log.completion_tokens = hit.usage.completion_tokens
                request_log.total_tokens = hit.usage.total_tokens
