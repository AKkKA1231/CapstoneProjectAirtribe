"""Operations reporting and recovery persistence contracts."""

from datetime import datetime
from typing import Protocol

from prism.domain.operations import (
    CacheStatistics,
    OpsFilters,
    Page,
    ProviderAttemptView,
    ReconciliationResult,
    RequestView,
    UsageSummary,
)


class OperationsRepository(Protocol):
    async def usage_summary(self, filters: OpsFilters) -> list[UsageSummary]: ...

    async def recent_requests(
        self, filters: OpsFilters, *, limit: int, offset: int
    ) -> Page[RequestView]: ...

    async def cache_statistics(
        self, filters: OpsFilters, now: datetime
    ) -> list[CacheStatistics]: ...

    async def provider_attempts(
        self, filters: OpsFilters, *, limit: int, offset: int
    ) -> Page[ProviderAttemptView]: ...


class ReconciliationRepository(Protocol):
    async def reconcile_stale(self, cutoff: datetime, now: datetime) -> ReconciliationResult: ...
