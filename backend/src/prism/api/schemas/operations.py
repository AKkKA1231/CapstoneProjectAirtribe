"""Public admin operations response models without credential material."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OpsModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UsageSummaryResponse(OpsModel):
    key_id: UUID
    key_prefix: str
    tenant_name: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Decimal
    cache_hits: int
    fallbacks: int


class RequestResponse(OpsModel):
    id: UUID
    key_id: UUID
    key_prefix: str
    tenant_name: str
    request_alias: str
    resolved_alias: str | None
    provider: str | None
    model: str | None
    status: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None
    cache_hit: bool
    fallback_triggered: bool
    auto_routed: bool
    routing_reason: str | None
    streamed: bool
    provider_latency_ms: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class CacheStatisticsResponse(OpsModel):
    key_id: UUID
    key_prefix: str
    tenant_name: str
    entry_count: int
    active_entries: int
    lookup_hits: int
    request_count: int
    request_cache_hits: int


class ProviderAttemptResponse(OpsModel):
    id: UUID
    request_log_id: UUID
    key_id: UUID
    key_prefix: str
    tenant_name: str
    provider: str
    model: str
    attempt_number: int
    retry_number: int
    outcome: str
    latency_ms: int | None
    http_status: int | None
    error_code: str | None
    cost_usd: Decimal | None
    started_at: datetime
    ended_at: datetime | None


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ReconciliationResponse(OpsModel):
    lock_acquired: bool
    examined: int
    released: int
    flagged: int
