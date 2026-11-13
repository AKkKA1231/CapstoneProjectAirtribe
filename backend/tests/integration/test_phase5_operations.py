"""Phase 5 PostgreSQL operations API and conservative reconciliation checks."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from prism.adapters.database.operations import (
    RECONCILIATION_ADVISORY_LOCK,
    PostgresOperationsRepository,
    PostgresReconciliationRepository,
)
from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import (
    AdmissionCounter,
    BudgetReservation,
    Provider,
    ProviderAttempt,
    RequestLog,
    VirtualKey,
)
from prism.api.dependencies import get_database
from prism.application.operations import ReconciliationService
from prism.domain.operations import OpsFilters
from prism.domain.security import digest_virtual_key
from prism.infrastructure.config import Settings
from prism.main import create_app

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


async def create_tenant(database: DatabaseManager, reserved: Decimal = Decimal(0)) -> UUID:
    key_id = uuid4()
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(
            VirtualKey(
                id=key_id,
                key_digest=digest_virtual_key(str(key_id), "phase5-pepper"),
                key_prefix=f"ops-{str(key_id)[:8]}",
                tenant_name=f"Ops tenant {key_id}",
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
                rpm_count=2,
                budget_month=now.strftime("%Y-%m"),
                spent_usd=Decimal("0.1"),
                reserved_usd=reserved,
            )
        )
    return key_id


async def cleanup(database: DatabaseManager, key_id: UUID) -> None:
    async with database.session() as session, session.begin():
        await session.execute(delete(RequestLog).where(RequestLog.key_id == key_id))
        await session.execute(delete(VirtualKey).where(VirtualKey.id == key_id))


async def provider_record(database: DatabaseManager) -> tuple[UUID, str]:
    async with database.session() as session:
        value = (
            await session.execute(
                select(Provider.id, Provider.name).order_by(Provider.name).limit(1)
            )
        ).one_or_none()
    assert value is not None
    return value.id, value.name


@pytest.mark.asyncio
async def test_ops_repository_and_admin_api_are_filtered_bounded_and_safe(
    database: DatabaseManager,
) -> None:
    key_id = await create_tenant(database)
    selected_provider, selected_provider_name = await provider_record(database)
    now = datetime.now(UTC)
    request_id = uuid4()
    async with database.session() as session, session.begin():
        session.add(
            RequestLog(
                id=request_id,
                key_id=key_id,
                request_alias="auto",
                resolved_alias="fast",
                provider_id=selected_provider,
                model="mock-fast-v1",
                status="success",
                request_fingerprint="f" * 64,
                prompt_tokens=4,
                completion_tokens=2,
                total_tokens=6,
                cost_usd=Decimal("0.000008"),
                cache_hit=True,
                fallback_triggered=True,
                auto_routed=True,
                routing_reason="default_low_complexity",
                streamed=False,
                provider_latency_ms=12,
                created_at=now,
                completed_at=now,
            )
        )
        await session.flush()
        session.add(
            ProviderAttempt(
                request_log_id=request_id,
                attempt_number=1,
                retry_number=0,
                provider_id=selected_provider,
                model="mock-fast-v1",
                outcome="success",
                prompt_tokens=4,
                completion_tokens=2,
                cost_usd=Decimal("0.000008"),
                started_at=now,
                ended_at=now,
                latency_ms=12,
                http_status=200,
            )
        )

    try:
        repository = PostgresOperationsRepository(database)
        filters = OpsFilters(
            tenant_id=key_id,
            status="success",
            provider=selected_provider_name,
            cache_hit=True,
            fallback=True,
        )
        usage = await repository.usage_summary(filters)
        requests = await repository.recent_requests(filters, limit=1, offset=0)
        attempts = await repository.provider_attempts(filters, limit=1, offset=0)
        cache = await repository.cache_statistics(OpsFilters(tenant_id=key_id), now)

        assert usage[0].request_count == 1
        assert usage[0].total_tokens == 6
        assert usage[0].cache_hits == 1
        assert usage[0].fallbacks == 1
        assert requests.total == 1 and len(requests.items) == 1
        assert attempts.total == 1 and attempts.items[0].provider == selected_provider_name
        assert cache[0].request_count == 1 and cache[0].request_cache_hits == 1
        assert not hasattr(requests.items[0], "request_fingerprint")
        assert not hasattr(requests.items[0], "key_digest")

        settings = Settings(
            environment="test",
            database_enabled=False,
            admin_api_key=SecretStr("phase5-admin"),
            reconciliation_enabled=False,
        )
        app = create_app(settings)
        app.dependency_overrides[get_database] = lambda: database
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get(
                "/ops/requests", headers={"Authorization": "Bearer prism-demo-team-alpha"}
            )
            bounded = await client.get(
                "/ops/requests?limit=101",
                headers={"Authorization": "Bearer phase5-admin"},
            )
            allowed = await client.get(
                f"/ops/requests?tenant_id={key_id}&limit=1",
                headers={"Authorization": "Bearer phase5-admin"},
            )
            denied_metrics = await client.get(
                "/metrics", headers={"Authorization": "Bearer prism-demo-team-alpha"}
            )
            metrics = await client.get("/metrics", headers={"Authorization": "Bearer phase5-admin"})

        assert denied.status_code == 401
        assert bounded.status_code == 422
        assert allowed.status_code == 200
        assert denied_metrics.status_code == 401
        assert metrics.status_code == 200
        assert "prism_http_requests_total" in metrics.text
        payload = allowed.json()
        assert payload["total"] == 1
        serialized = str(payload)
        assert "request_fingerprint" not in serialized
        assert "key_digest" not in serialized
    finally:
        await cleanup(database, key_id)


@pytest.mark.asyncio
async def test_reconciler_lock_release_and_ambiguous_attempt_flagging(
    database: DatabaseManager,
) -> None:
    key_id = await create_tenant(database, Decimal("0.6"))
    selected_provider, _ = await provider_record(database)
    now = datetime.now(UTC)
    stale = now - timedelta(hours=1)
    safe_request = uuid4()
    ambiguous_request = uuid4()
    async with database.session() as session, session.begin():
        for request_id in (safe_request, ambiguous_request):
            session.add(
                RequestLog(
                    id=request_id,
                    key_id=key_id,
                    request_alias="fast",
                    status="in_progress",
                    request_fingerprint=str(request_id).replace("-", "").ljust(64, "0"),
                    created_at=stale,
                )
            )
            await session.flush()
            session.add(
                BudgetReservation(
                    key_id=key_id,
                    request_log_id=request_id,
                    reserved_usd=Decimal("0.3"),
                    status="pending",
                    created_at=stale,
                    updated_at=stale,
                )
            )
        session.add(
            ProviderAttempt(
                request_log_id=ambiguous_request,
                attempt_number=1,
                retry_number=0,
                provider_id=selected_provider,
                model="mock-fast-v1",
                outcome="timeout",
                started_at=stale,
                ended_at=stale,
                error_code="provider_timeout",
            )
        )

    repository = PostgresReconciliationRepository(database)
    try:
        async with database.session() as lock_session, lock_session.begin():
            await lock_session.scalar(
                select(func.pg_advisory_xact_lock(RECONCILIATION_ADVISORY_LOCK))
            )
            skipped = await repository.reconcile_stale(now - timedelta(minutes=15), now)
            assert skipped.lock_acquired is False

        result = await ReconciliationService(repository, 900).run(now)
        assert result.lock_acquired is True
        assert (result.examined, result.released, result.flagged) == (2, 1, 1)

        async with database.session() as session:
            counter = await session.get(AdmissionCounter, key_id)
            safe = await session.scalar(
                select(BudgetReservation).where(BudgetReservation.request_log_id == safe_request)
            )
            ambiguous = await session.scalar(
                select(BudgetReservation).where(
                    BudgetReservation.request_log_id == ambiguous_request
                )
            )
            ambiguous_log = await session.get(RequestLog, ambiguous_request)
            assert counter is not None and counter.reserved_usd == Decimal("0.3000000000")
            assert safe is not None and safe.status == "cancelled"
            assert ambiguous is not None and ambiguous.status == "reconciliation_required"
            assert ambiguous.reconciliation_required is True
            assert ambiguous_log is not None
            assert ambiguous_log.error_code == "ambiguous_billable_attempt"
    finally:
        await cleanup(database, key_id)
