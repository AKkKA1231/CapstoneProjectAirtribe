"""Semantic-cache values independent of persistence and embedding libraries."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from prism.domain.providers import TokenUsage


@dataclass(frozen=True, slots=True)
class CacheQuery:
    scope_hash: str
    request_fingerprint: str
    prompt_text: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class CacheHit:
    entry_id: UUID
    response_body: dict[str, Any]
    model: str
    provider: str
    usage: TokenUsage | None
    original_cost_usd: Decimal | None
    similarity: float
