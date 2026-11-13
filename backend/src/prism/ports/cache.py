"""Tenant-scoped semantic-cache persistence port."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from prism.domain.cache import CacheHit, CacheQuery
from prism.domain.providers import TokenUsage


class CacheRepository(Protocol):
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
    ) -> CacheHit | None: ...

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
    ) -> None: ...

    async def mark_request_hit(
        self,
        *,
        request_log_id: UUID,
        hit: CacheHit,
        resolved_alias: str,
        streamed: bool,
        now: datetime,
    ) -> None: ...
