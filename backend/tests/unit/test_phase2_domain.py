"""Focused tests for Phase 2 security and accounting policies."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from prism.application.accounting import AccountingService
from prism.application.security import AuthenticationService, authenticate_admin
from prism.domain.accounting import Admission, Pricing, Settlement
from prism.domain.errors import (
    ExpiredApiKeyError,
    InactiveApiKeyError,
    InvalidAdminKeyError,
    InvalidApiKeyError,
    ModelNotAllowedError,
)
from prism.domain.security import TenantContext, VirtualKeyRecord, digest_virtual_key


class FakeSecurityRepository:
    def __init__(self, record: VirtualKeyRecord | None) -> None:
        self.record = record
        self.seen_digest: str | None = None

    async def find_virtual_key(self, digest: str) -> VirtualKeyRecord | None:
        self.seen_digest = digest
        return self.record

    async def record_security_event(self, **_: object) -> None:
        return None


class FakeAccountingRepository:
    def __init__(self) -> None:
        self.pricing = Pricing(Decimal("0.001"), Decimal("0.002"))
        self.admitted: tuple[str, Decimal] | None = None

    async def get_pricing(
        self, provider_id: object, model: str, effective_at: datetime
    ) -> Pricing | None:
        return self.pricing

    async def admit(self, **values: object) -> Admission:
        self.admitted = (str(values["alias"]), values["reserved_usd"])  # type: ignore[arg-type]
        return Admission(uuid4(), uuid4(), values["reserved_usd"])  # type: ignore[arg-type]

    async def settle(
        self, reservation_id: object, actual_cost_usd: Decimal, now: datetime
    ) -> Settlement:
        return Settlement(reservation_id, "settled", actual_cost_usd, True)  # type: ignore[arg-type]

    async def release(self, reservation_id: object, now: datetime, reason: str) -> Settlement:
        return Settlement(reservation_id, "cancelled", Decimal(0), True)  # type: ignore[arg-type]


def record(*, active: bool = True, expires_at: datetime | None = None) -> VirtualKeyRecord:
    return VirtualKeyRecord(
        id=uuid4(),
        tenant_name="Tenant",
        rpm_limit=10,
        monthly_budget_usd=Decimal("1"),
        is_active=active,
        expires_at=expires_at,
        allowed_aliases=frozenset({"fast"}),
    )


@pytest.mark.asyncio
async def test_valid_key_authenticates_with_pepper_scoped_digest() -> None:
    repository = FakeSecurityRepository(record())
    service = AuthenticationService(repository, "pepper")
    now = datetime.now(UTC)

    tenant = await service.authenticate("secret-key", now)

    assert tenant.tenant_name == "Tenant"
    assert repository.seen_digest == digest_virtual_key("secret-key", "pepper")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_record", "error"),
    [
        (None, InvalidApiKeyError),
        (record(active=False), InactiveApiKeyError),
        (record(expires_at=datetime.now(UTC) - timedelta(seconds=1)), ExpiredApiKeyError),
    ],
)
async def test_invalid_key_states_are_distinct(
    stored_record: VirtualKeyRecord | None, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await AuthenticationService(FakeSecurityRepository(stored_record), "pepper").authenticate(
            "secret-key", datetime.now(UTC)
        )


def test_alias_allowlist_and_admin_credentials_are_separate() -> None:
    tenant = TenantContext(uuid4(), "Tenant", 10, Decimal("1"), frozenset({"fast"}))
    tenant.authorize_alias("fast")
    with pytest.raises(ModelNotAllowedError):
        tenant.authorize_alias("smart")
    authenticate_admin("admin-key", "admin-key")
    with pytest.raises(InvalidAdminKeyError):
        authenticate_admin("tenant-key", "admin-key")


@pytest.mark.asyncio
async def test_cost_estimate_is_decimal_and_admission_checks_alias() -> None:
    repository = FakeAccountingRepository()
    service = AccountingService(repository)  # type: ignore[arg-type]
    tenant = TenantContext(uuid4(), "Tenant", 10, Decimal("1"), frozenset({"fast"}))
    now = datetime.now(UTC)

    estimate = await service.estimate_cost(
        provider_id=uuid4(),
        model="model",
        input_tokens=125,
        maximum_output_tokens=250,
        now=now,
    )
    admission = await service.admit(
        tenant=tenant,
        alias="fast",
        reserved_usd=estimate,
        request_fingerprint="a" * 64,
        streamed=False,
        now=now,
    )

    assert estimate == Decimal("0.0006250000")
    assert admission.reserved_usd == estimate
    assert repository.admitted == ("fast", estimate)
    with pytest.raises(ModelNotAllowedError):
        await service.admit(
            tenant=tenant,
            alias="smart",
            reserved_usd=estimate,
            request_fingerprint="b" * 64,
            streamed=False,
            now=now,
        )
