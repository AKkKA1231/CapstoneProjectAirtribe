"""Admission, reservation, and exactly-once settlement orchestration."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from prism.domain.accounting import Admission, Settlement, money
from prism.domain.errors import PricingNotFoundError
from prism.domain.security import TenantContext
from prism.infrastructure.metrics import MetricsRegistry
from prism.ports.repositories import AccountingRepository


class AccountingService:
    def __init__(
        self, repository: AccountingRepository, metrics: MetricsRegistry | None = None
    ) -> None:
        self._repository = repository
        self._metrics = metrics

    async def estimate_cost(
        self,
        *,
        provider_id: UUID,
        model: str,
        input_tokens: int,
        maximum_output_tokens: int,
        now: datetime,
    ) -> Decimal:
        pricing = await self._repository.get_pricing(provider_id, model, now)
        if pricing is None:
            raise PricingNotFoundError(f"No active pricing exists for model {model!r}")
        return pricing.estimate(input_tokens, maximum_output_tokens)

    async def admit(
        self,
        *,
        tenant: TenantContext,
        alias: str,
        reserved_usd: Decimal,
        request_fingerprint: str,
        streamed: bool,
        now: datetime,
    ) -> Admission:
        tenant.authorize_alias(alias)
        return await self._repository.admit(
            tenant=tenant,
            alias=alias,
            reserved_usd=money(reserved_usd),
            request_fingerprint=request_fingerprint,
            streamed=streamed,
            now=now,
        )

    async def settle(
        self,
        reservation_id: UUID,
        actual_cost_usd: Decimal,
        now: datetime,
        request_status: str = "success",
    ) -> Settlement:
        try:
            return await self._repository.settle(
                reservation_id, money(actual_cost_usd), now, request_status
            )
        except Exception:
            if self._metrics is not None:
                self._metrics.increment("settlement_failures_total", operation="settle")
            raise

    async def release(
        self,
        reservation_id: UUID,
        now: datetime,
        reason: str,
        request_status: str = "cancelled",
        error_code: str | None = "reservation_released",
    ) -> Settlement:
        try:
            return await self._repository.release(
                reservation_id, now, reason, request_status, error_code
            )
        except Exception:
            if self._metrics is not None:
                self._metrics.increment("settlement_failures_total", operation="release")
            raise
