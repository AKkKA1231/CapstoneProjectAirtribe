"""Persistence ports used by Phase 2 application services."""

from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from prism.domain.accounting import Admission, Pricing, Settlement
from prism.domain.providers import ProviderAttemptResult, ProviderTarget, TokenUsage
from prism.domain.routing import RoutingDecision
from prism.domain.security import TenantContext, VirtualKeyRecord


class SecurityRepository(Protocol):
    async def find_virtual_key(self, digest: str) -> VirtualKeyRecord | None: ...

    async def record_security_event(
        self,
        *,
        event_type: str,
        key_fingerprint: str | None,
        request_id: str,
        client_ip_hash: str | None,
    ) -> None: ...


class AccountingRepository(Protocol):
    async def get_pricing(
        self, provider_id: UUID, model: str, effective_at: datetime
    ) -> Pricing | None: ...

    async def admit(
        self,
        *,
        tenant: TenantContext,
        alias: str,
        reserved_usd: Decimal,
        request_fingerprint: str,
        streamed: bool,
        now: datetime,
    ) -> Admission: ...

    async def settle(
        self,
        reservation_id: UUID,
        actual_cost_usd: Decimal,
        now: datetime,
        request_status: str = "success",
    ) -> Settlement: ...

    async def release(
        self,
        reservation_id: UUID,
        now: datetime,
        reason: str,
        request_status: str = "cancelled",
        error_code: str | None = "reservation_released",
    ) -> Settlement: ...


class ProviderRepository(Protocol):
    async def resolve_targets(self, alias: str, effective_at: datetime) -> list[ProviderTarget]: ...

    async def record_attempt(self, request_log_id: UUID, result: ProviderAttemptResult) -> None: ...

    async def update_request_execution(
        self,
        *,
        request_log_id: UUID,
        target: ProviderTarget,
        usage: TokenUsage | None,
        fallback_triggered: bool,
        latency_ms: int,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def update_routing_decision(
        self,
        request_log_id: UUID,
        resolved_alias: str,
        decision: RoutingDecision,
    ) -> None: ...
