"""PostgreSQL verification for Phase 2 row-lock and settlement invariants."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from prism.adapters.database.repositories import (
    PostgresAccountingRepository,
    PostgresSecurityRepository,
)
from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import (
    AdmissionCounter,
    BudgetReservation,
    ModelAlias,
    RequestLog,
    VirtualKey,
    VirtualKeyAllowedAlias,
)
from prism.application.accounting import AccountingService
from prism.application.security import AuthenticationService
from prism.domain.errors import BudgetExceededError, RateLimitExceededError
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
            database_pool_size=20,
            database_max_overflow=20,
        )
    )
    yield manager
    await manager.close()


async def create_tenant(
    database: DatabaseManager,
    *,
    budget: Decimal = Decimal("1"),
    rpm_limit: int = 100,
    pepper: str = "integration-pepper",
) -> tuple[str, TenantContext]:
    key_id = uuid4()
    raw_key = f"integration-{key_id}"
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        alias_id = await session.scalar(select(ModelAlias.id).where(ModelAlias.alias == "fast"))
        assert alias_id is not None
        session.add(
            VirtualKey(
                id=key_id,
                key_digest=digest_virtual_key(raw_key, pepper),
                key_prefix=raw_key[:12],
                tenant_name=f"Integration {key_id}",
                rpm_limit=rpm_limit,
                monthly_budget_usd=budget,
                is_active=True,
            )
        )
        # Explicit flush documents the parent-before-dependent fixture invariant.
        await session.flush()
        session.add(VirtualKeyAllowedAlias(key_id=key_id, alias_id=alias_id))
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
    return raw_key, TenantContext(
        key_id,
        f"Integration {key_id}",
        rpm_limit,
        budget,
        frozenset({"fast"}),
    )


async def delete_tenant(database: DatabaseManager, key_id: UUID) -> None:
    async with database.session() as session, session.begin():
        await session.execute(delete(RequestLog).where(RequestLog.key_id == key_id))
        await session.execute(delete(VirtualKey).where(VirtualKey.id == key_id))


@pytest.mark.asyncio
async def test_postgres_authentication_reads_alias_allowlist(database: DatabaseManager) -> None:
    raw_key, expected = await create_tenant(database)
    try:
        actual = await AuthenticationService(
            PostgresSecurityRepository(database), "integration-pepper"
        ).authenticate(raw_key, datetime.now(UTC))
        assert actual == expected
    finally:
        await delete_tenant(database, expected.key_id)


@pytest.mark.asyncio
async def test_concurrent_requests_never_over_admit_budget(database: DatabaseManager) -> None:
    _, tenant = await create_tenant(database, budget=Decimal("1"))
    service = AccountingService(PostgresAccountingRepository(database))

    async def attempt(index: int) -> object:
        try:
            return await service.admit(
                tenant=tenant,
                alias="fast",
                reserved_usd=Decimal("0.3"),
                request_fingerprint=f"{index:064d}",
                streamed=False,
                now=datetime.now(UTC),
            )
        except BudgetExceededError as error:
            return error

    try:
        results = await asyncio.gather(*(attempt(index) for index in range(10)))
        admitted = [result for result in results if not isinstance(result, Exception)]
        rejected = [result for result in results if isinstance(result, BudgetExceededError)]

        assert len(admitted) == 3
        assert len(rejected) == 7
        async with database.session() as session:
            counter = await session.get(AdmissionCounter, tenant.key_id)
            reservations = (
                await session.scalars(
                    select(BudgetReservation).where(BudgetReservation.key_id == tenant.key_id)
                )
            ).all()
            assert counter is not None
            assert counter.reserved_usd == Decimal("0.9000000000")
            assert len(reservations) == 3
    finally:
        await delete_tenant(database, tenant.key_id)


@pytest.mark.asyncio
async def test_rpm_reset_month_rollover_and_independent_tenants(
    database: DatabaseManager,
) -> None:
    _, first = await create_tenant(database, rpm_limit=1)
    _, second = await create_tenant(database, rpm_limit=1)
    service = AccountingService(PostgresAccountingRepository(database))
    now = datetime.now(UTC)

    try:
        await asyncio.gather(
            service.admit(
                tenant=first,
                alias="fast",
                reserved_usd=Decimal("0.1"),
                request_fingerprint="a" * 64,
                streamed=False,
                now=now,
            ),
            service.admit(
                tenant=second,
                alias="fast",
                reserved_usd=Decimal("0.1"),
                request_fingerprint="b" * 64,
                streamed=False,
                now=now,
            ),
        )
        with pytest.raises(RateLimitExceededError):
            await service.admit(
                tenant=first,
                alias="fast",
                reserved_usd=Decimal("0.1"),
                request_fingerprint="c" * 64,
                streamed=False,
                now=now,
            )

        async with database.session() as session, session.begin():
            counter = await session.get(AdmissionCounter, first.key_id, with_for_update=True)
            assert counter is not None
            counter.rpm_window_start = now - timedelta(minutes=2)
            counter.budget_month = "2000-01"
            counter.spent_usd = Decimal("0.9")
            counter.reserved_usd = Decimal(0)

        await service.admit(
            tenant=first,
            alias="fast",
            reserved_usd=Decimal("0.2"),
            request_fingerprint="d" * 64,
            streamed=False,
            now=now,
        )
    finally:
        await delete_tenant(database, first.key_id)
        await delete_tenant(database, second.key_id)


@pytest.mark.asyncio
async def test_settlement_and_release_are_exactly_once(database: DatabaseManager) -> None:
    _, tenant = await create_tenant(database)
    service = AccountingService(PostgresAccountingRepository(database))
    now = datetime.now(UTC)
    try:
        settled_admission = await service.admit(
            tenant=tenant,
            alias="fast",
            reserved_usd=Decimal("0.4"),
            request_fingerprint="e" * 64,
            streamed=False,
            now=now,
        )
        released_admission = await service.admit(
            tenant=tenant,
            alias="fast",
            reserved_usd=Decimal("0.4"),
            request_fingerprint="f" * 64,
            streamed=False,
            now=now,
        )

        first_settlement = await service.settle(
            settled_admission.reservation_id, Decimal("0.25"), now
        )
        repeated_settlement = await service.settle(
            settled_admission.reservation_id, Decimal("0.25"), now
        )
        first_release = await service.release(released_admission.reservation_id, now, "failed")
        repeated_release = await service.release(released_admission.reservation_id, now, "failed")

        assert first_settlement.changed is True
        assert repeated_settlement.changed is False
        assert first_release.changed is True
        assert repeated_release.changed is False
        async with database.session() as session:
            counter = await session.get(AdmissionCounter, tenant.key_id)
            assert counter is not None
            assert counter.reserved_usd == Decimal("0E-10")
            assert counter.spent_usd == Decimal("0.2500000000")
    finally:
        await delete_tenant(database, tenant.key_id)
