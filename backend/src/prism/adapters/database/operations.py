"""PostgreSQL operations reporting and advisory-lock reconciliation."""

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import case, func, select
from sqlalchemy.sql.elements import ColumnElement

from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import (
    AdmissionCounter,
    BudgetReservation,
    CacheEntry,
    Provider,
    ProviderAttempt,
    RequestLog,
    VirtualKey,
)
from prism.domain.operations import (
    CacheStatistics,
    OpsFilters,
    Page,
    ProviderAttemptView,
    ReconciliationResult,
    RequestView,
    UsageSummary,
)

RECONCILIATION_ADVISORY_LOCK = 6_279_472_013


def _request_conditions(filters: OpsFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.tenant_id is not None:
        conditions.append(RequestLog.key_id == filters.tenant_id)
    if filters.start_at is not None:
        conditions.append(RequestLog.created_at >= filters.start_at)
    if filters.end_at is not None:
        conditions.append(RequestLog.created_at < filters.end_at)
    if filters.status is not None:
        conditions.append(RequestLog.status == filters.status)
    if filters.provider is not None:
        conditions.append(Provider.name == filters.provider)
    if filters.cache_hit is not None:
        conditions.append(RequestLog.cache_hit == filters.cache_hit)
    if filters.fallback is not None:
        conditions.append(RequestLog.fallback_triggered == filters.fallback)
    return conditions


class PostgresOperationsRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def usage_summary(self, filters: OpsFilters) -> list[UsageSummary]:
        conditions = _request_conditions(filters)
        statement = (
            select(
                VirtualKey.id.label("key_id"),
                VirtualKey.key_prefix,
                VirtualKey.tenant_name,
                func.count(RequestLog.id).label("request_count"),
                func.coalesce(func.sum(RequestLog.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(RequestLog.completion_tokens), 0).label("completion_tokens"),
                func.coalesce(func.sum(RequestLog.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0).label("cost_usd"),
                func.coalesce(
                    func.sum(case((RequestLog.cache_hit.is_(True), 1), else_=0)), 0
                ).label("cache_hits"),
                func.coalesce(
                    func.sum(case((RequestLog.fallback_triggered.is_(True), 1), else_=0)), 0
                ).label("fallbacks"),
            )
            .select_from(RequestLog)
            .join(VirtualKey, VirtualKey.id == RequestLog.key_id)
            .outerjoin(Provider, Provider.id == RequestLog.provider_id)
            .where(*conditions)
            .group_by(VirtualKey.id, VirtualKey.key_prefix, VirtualKey.tenant_name)
            .order_by(VirtualKey.tenant_name)
        )
        async with self._database.session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [
            UsageSummary(
                key_id=row["key_id"],
                key_prefix=row["key_prefix"],
                tenant_name=row["tenant_name"],
                request_count=int(row["request_count"]),
                prompt_tokens=int(row["prompt_tokens"]),
                completion_tokens=int(row["completion_tokens"]),
                total_tokens=int(row["total_tokens"]),
                cost_usd=Decimal(row["cost_usd"]),
                cache_hits=int(row["cache_hits"]),
                fallbacks=int(row["fallbacks"]),
            )
            for row in rows
        ]

    async def recent_requests(
        self, filters: OpsFilters, *, limit: int, offset: int
    ) -> Page[RequestView]:
        conditions = _request_conditions(filters)
        base = (
            select(RequestLog)
            .select_from(RequestLog)
            .join(VirtualKey, VirtualKey.id == RequestLog.key_id)
            .outerjoin(Provider, Provider.id == RequestLog.provider_id)
            .where(*conditions)
        )
        count_statement = select(func.count()).select_from(base.subquery())
        statement = (
            select(
                RequestLog,
                VirtualKey.key_prefix,
                VirtualKey.tenant_name,
                Provider.name.label("provider_name"),
            )
            .select_from(RequestLog)
            .join(VirtualKey, VirtualKey.id == RequestLog.key_id)
            .outerjoin(Provider, Provider.id == RequestLog.provider_id)
            .where(*conditions)
            .order_by(RequestLog.created_at.desc(), RequestLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session() as session:
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.execute(statement)).all()
        items = []
        for row in rows:
            log = row[0]
            items.append(
                RequestView(
                    id=log.id,
                    key_id=log.key_id,
                    key_prefix=row[1],
                    tenant_name=row[2],
                    request_alias=log.request_alias,
                    resolved_alias=log.resolved_alias,
                    provider=cast(str | None, row[3]),
                    model=log.model,
                    status=log.status,
                    prompt_tokens=log.prompt_tokens,
                    completion_tokens=log.completion_tokens,
                    total_tokens=log.total_tokens,
                    cost_usd=log.cost_usd,
                    cache_hit=log.cache_hit,
                    fallback_triggered=log.fallback_triggered,
                    auto_routed=log.auto_routed,
                    routing_reason=log.routing_reason,
                    streamed=log.streamed,
                    provider_latency_ms=log.provider_latency_ms,
                    error_code=log.error_code,
                    created_at=log.created_at,
                    completed_at=log.completed_at,
                )
            )
        return Page(items, total, limit, offset)

    async def cache_statistics(self, filters: OpsFilters, now: datetime) -> list[CacheStatistics]:
        entry_count = (
            select(func.count(CacheEntry.id))
            .where(CacheEntry.key_id == VirtualKey.id)
            .correlate(VirtualKey)
            .scalar_subquery()
        )
        active_entries = (
            select(func.count(CacheEntry.id))
            .where(CacheEntry.key_id == VirtualKey.id, CacheEntry.expires_at > now)
            .correlate(VirtualKey)
            .scalar_subquery()
        )
        lookup_hits = (
            select(func.coalesce(func.sum(CacheEntry.hit_count), 0))
            .where(CacheEntry.key_id == VirtualKey.id)
            .correlate(VirtualKey)
            .scalar_subquery()
        )
        request_conditions = _request_conditions(filters)
        request_conditions.append(RequestLog.key_id == VirtualKey.id)
        request_count = (
            select(func.count(RequestLog.id))
            .select_from(RequestLog)
            .outerjoin(Provider, Provider.id == RequestLog.provider_id)
            .where(*request_conditions)
            .correlate(VirtualKey)
            .scalar_subquery()
        )
        request_hits = (
            select(func.count(RequestLog.id))
            .select_from(RequestLog)
            .outerjoin(Provider, Provider.id == RequestLog.provider_id)
            .where(*request_conditions, RequestLog.cache_hit.is_(True))
            .correlate(VirtualKey)
            .scalar_subquery()
        )
        statement = select(
            VirtualKey.id,
            VirtualKey.key_prefix,
            VirtualKey.tenant_name,
            entry_count.label("entry_count"),
            active_entries.label("active_entries"),
            lookup_hits.label("lookup_hits"),
            request_count.label("request_count"),
            request_hits.label("request_cache_hits"),
        ).order_by(VirtualKey.tenant_name)
        if filters.tenant_id is not None:
            statement = statement.where(VirtualKey.id == filters.tenant_id)
        async with self._database.session() as session:
            rows = (await session.execute(statement)).all()
        return [
            CacheStatistics(
                key_id=cast(Any, row[0]),
                key_prefix=cast(str, row[1]),
                tenant_name=cast(str, row[2]),
                entry_count=int(row[3]),
                active_entries=int(row[4]),
                lookup_hits=int(row[5]),
                request_count=int(row[6]),
                request_cache_hits=int(row[7]),
            )
            for row in rows
        ]

    async def provider_attempts(
        self, filters: OpsFilters, *, limit: int, offset: int
    ) -> Page[ProviderAttemptView]:
        conditions = _request_conditions(filters)
        base = (
            select(ProviderAttempt.id)
            .select_from(ProviderAttempt)
            .join(RequestLog, RequestLog.id == ProviderAttempt.request_log_id)
            .join(VirtualKey, VirtualKey.id == RequestLog.key_id)
            .join(Provider, Provider.id == ProviderAttempt.provider_id)
            .where(*conditions)
        )
        statement = (
            select(
                ProviderAttempt,
                RequestLog.key_id,
                VirtualKey.key_prefix,
                VirtualKey.tenant_name,
                Provider.name,
            )
            .select_from(ProviderAttempt)
            .join(RequestLog, RequestLog.id == ProviderAttempt.request_log_id)
            .join(VirtualKey, VirtualKey.id == RequestLog.key_id)
            .join(Provider, Provider.id == ProviderAttempt.provider_id)
            .where(*conditions)
            .order_by(ProviderAttempt.started_at.desc(), ProviderAttempt.id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._database.session() as session:
            total = int(
                await session.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (await session.execute(statement)).all()
        items = []
        for row in rows:
            attempt = row[0]
            items.append(
                ProviderAttemptView(
                    id=attempt.id,
                    request_log_id=attempt.request_log_id,
                    key_id=cast(Any, row[1]),
                    key_prefix=row[2],
                    tenant_name=row[3],
                    provider=row[4],
                    model=attempt.model,
                    attempt_number=attempt.attempt_number,
                    retry_number=attempt.retry_number,
                    outcome=attempt.outcome,
                    latency_ms=attempt.latency_ms,
                    http_status=attempt.http_status,
                    error_code=attempt.error_code,
                    cost_usd=attempt.cost_usd,
                    started_at=attempt.started_at,
                    ended_at=attempt.ended_at,
                )
            )
        return Page(items, total, limit, offset)


class PostgresReconciliationRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def reconcile_stale(self, cutoff: datetime, now: datetime) -> ReconciliationResult:
        async with self._database.session() as session, session.begin():
            lock_acquired = bool(
                await session.scalar(
                    select(func.pg_try_advisory_xact_lock(RECONCILIATION_ADVISORY_LOCK))
                )
            )
            if not lock_acquired:
                return ReconciliationResult(False, 0, 0, 0)

            reservations = (
                await session.scalars(
                    select(BudgetReservation)
                    .where(
                        BudgetReservation.status == "pending",
                        BudgetReservation.created_at < cutoff,
                    )
                    .order_by(BudgetReservation.created_at)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            released = 0
            flagged = 0
            for reservation in reservations:
                attempt_count = int(
                    await session.scalar(
                        select(func.count(ProviderAttempt.id)).where(
                            ProviderAttempt.request_log_id == reservation.request_log_id
                        )
                    )
                    or 0
                )
                request_log = await session.get(RequestLog, reservation.request_log_id)
                if attempt_count:
                    reservation.status = "reconciliation_required"
                    reservation.reconciliation_required = True
                    reservation.last_error = "potentially billable provider attempt requires review"
                    reservation.updated_at = now
                    if request_log is not None:
                        request_log.status = "error"
                        request_log.error_code = "ambiguous_billable_attempt"
                        request_log.error_message = "manual accounting reconciliation required"
                        request_log.completed_at = now
                    flagged += 1
                    continue

                counter = await session.get(
                    AdmissionCounter, reservation.key_id, with_for_update=True
                )
                if counter is None:
                    reservation.status = "reconciliation_required"
                    reservation.reconciliation_required = True
                    reservation.last_error = "admission counter missing"
                    reservation.updated_at = now
                    flagged += 1
                    continue
                if counter.budget_month == reservation.created_at.strftime("%Y-%m"):
                    counter.reserved_usd = max(
                        Decimal(0), counter.reserved_usd - reservation.reserved_usd
                    )
                    counter.updated_at = now
                reservation.status = "cancelled"
                reservation.settled_usd = Decimal(0)
                reservation.settled_at = now
                reservation.updated_at = now
                if request_log is not None:
                    request_log.status = "cancelled"
                    request_log.cost_usd = Decimal(0)
                    request_log.error_code = "stale_reservation_released"
                    request_log.error_message = "abandoned before provider execution"
                    request_log.completed_at = now
                released += 1

            return ReconciliationResult(True, len(reservations), released, flagged)
