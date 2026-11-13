"""Admin-only operational visibility and recovery endpoints."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from prism.adapters.database.operations import (
    PostgresOperationsRepository,
    PostgresReconciliationRepository,
)
from prism.api.dependencies import AdminDependency, DatabaseDependency, SettingsDependency
from prism.api.schemas.operations import (
    CacheStatisticsResponse,
    PageResponse,
    ProviderAttemptResponse,
    ReconciliationResponse,
    RequestResponse,
    UsageSummaryResponse,
)
from prism.application.operations import OperationsService, ReconciliationService
from prism.domain.errors import DatabaseUnavailableError
from prism.domain.operations import OpsFilters

router = APIRouter(prefix="/ops", tags=["operations"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def _service(database: DatabaseDependency) -> OperationsService:
    if database is None:
        raise DatabaseUnavailableError("The operations database is unavailable")
    return OperationsService(PostgresOperationsRepository(database))


def _filters(
    tenant_id: UUID | None,
    start_at: datetime | None,
    end_at: datetime | None,
    status: str | None,
    provider: str | None,
    cache_hit: bool | None,
    fallback: bool | None,
) -> OpsFilters:
    return OpsFilters(tenant_id, start_at, end_at, status, provider, cache_hit, fallback)


@router.get("/usage", response_model=list[UsageSummaryResponse])
async def usage_summary(
    _: AdminDependency,
    database: DatabaseDependency,
    tenant_id: UUID | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    status: str | None = Query(default=None, max_length=40),
    provider: str | None = Query(default=None, max_length=100),
    cache_hit: bool | None = None,
    fallback: bool | None = None,
) -> list[UsageSummaryResponse]:
    items = await _service(database).usage(
        _filters(tenant_id, start_at, end_at, status, provider, cache_hit, fallback)
    )
    return [UsageSummaryResponse.model_validate(item) for item in items]


@router.get("/requests", response_model=PageResponse[RequestResponse])
async def recent_requests(
    _: AdminDependency,
    database: DatabaseDependency,
    limit: Limit = 50,
    offset: Offset = 0,
    tenant_id: UUID | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    status: str | None = Query(default=None, max_length=40),
    provider: str | None = Query(default=None, max_length=100),
    cache_hit: bool | None = None,
    fallback: bool | None = None,
) -> PageResponse[RequestResponse]:
    page = await _service(database).requests(
        _filters(tenant_id, start_at, end_at, status, provider, cache_hit, fallback),
        limit=limit,
        offset=offset,
    )
    return PageResponse[RequestResponse](
        items=[RequestResponse.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/cache", response_model=list[CacheStatisticsResponse])
async def cache_statistics(
    _: AdminDependency,
    database: DatabaseDependency,
    tenant_id: UUID | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> list[CacheStatisticsResponse]:
    items = await _service(database).cache(
        _filters(tenant_id, start_at, end_at, None, None, None, None),
        datetime.now(UTC),
    )
    return [CacheStatisticsResponse.model_validate(item) for item in items]


@router.get("/provider-attempts", response_model=PageResponse[ProviderAttemptResponse])
async def provider_attempts(
    _: AdminDependency,
    database: DatabaseDependency,
    limit: Limit = 50,
    offset: Offset = 0,
    tenant_id: UUID | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    status: str | None = Query(default=None, max_length=40),
    provider: str | None = Query(default=None, max_length=100),
    cache_hit: bool | None = None,
    fallback: bool | None = None,
) -> PageResponse[ProviderAttemptResponse]:
    page = await _service(database).attempts(
        _filters(tenant_id, start_at, end_at, status, provider, cache_hit, fallback),
        limit=limit,
        offset=offset,
    )
    return PageResponse[ProviderAttemptResponse](
        items=[ProviderAttemptResponse.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/reconcile", response_model=ReconciliationResponse)
async def reconcile_stale_reservations(
    _: AdminDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ReconciliationResponse:
    if database is None:
        raise DatabaseUnavailableError("The reconciliation database is unavailable")
    result = await ReconciliationService(
        PostgresReconciliationRepository(database), settings.reconciliation_stale_seconds
    ).run(datetime.now(UTC))
    return ReconciliationResponse.model_validate(result)
