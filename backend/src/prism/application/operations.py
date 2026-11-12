"""Operations reporting and stale-reservation recovery services."""

from datetime import datetime, timedelta

from prism.domain.operations import (
    CacheStatistics,
    OpsFilters,
    Page,
    ProviderAttemptView,
    ReconciliationResult,
    RequestView,
    UsageSummary,
)
from prism.ports.operations import OperationsRepository, ReconciliationRepository


class OperationsService:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository

    async def usage(self, filters: OpsFilters) -> list[UsageSummary]:
        return await self._repository.usage_summary(filters)

    async def requests(self, filters: OpsFilters, *, limit: int, offset: int) -> Page[RequestView]:
        return await self._repository.recent_requests(filters, limit=limit, offset=offset)

    async def cache(self, filters: OpsFilters, now: datetime) -> list[CacheStatistics]:
        return await self._repository.cache_statistics(filters, now)

    async def attempts(
        self, filters: OpsFilters, *, limit: int, offset: int
    ) -> Page[ProviderAttemptView]:
        return await self._repository.provider_attempts(filters, limit=limit, offset=offset)


class ReconciliationService:
    def __init__(self, repository: ReconciliationRepository, stale_after_seconds: int) -> None:
        self._repository = repository
        self._stale_after = timedelta(seconds=stale_after_seconds)

    async def run(self, now: datetime) -> ReconciliationResult:
        return await self._repository.reconcile_stale(now - self._stale_after, now)
