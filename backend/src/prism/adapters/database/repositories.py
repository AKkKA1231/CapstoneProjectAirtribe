"""PostgreSQL adapters for authentication and atomic accounting."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select

from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import (
    AdmissionCounter,
    BudgetReservation,
    FallbackTarget,
    ModelAlias,
    ModelPricing,
    Provider,
    ProviderAttempt,
    RequestLog,
    SecurityEvent,
    VirtualKey,
    VirtualKeyAllowedAlias,
)
from prism.domain.accounting import Admission, Pricing, Settlement
from prism.domain.errors import (
    AliasNotFoundError,
    BudgetExceededError,
    PricingNotFoundError,
    RateLimitExceededError,
    RoutingNotImplementedError,
)
from prism.domain.providers import ProviderAttemptResult, ProviderTarget, TokenUsage
from prism.domain.routing import RoutingDecision
from prism.domain.security import TenantContext, VirtualKeyRecord
from prism.infrastructure.metrics import MetricsRegistry


class PostgresSecurityRepository:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def find_virtual_key(self, digest: str) -> VirtualKeyRecord | None:
        async with self._database.session() as session:
            key = await session.scalar(select(VirtualKey).where(VirtualKey.key_digest == digest))
            if key is None:
                return None
            aliases = (
                await session.scalars(
                    select(ModelAlias.alias)
                    .join(
                        VirtualKeyAllowedAlias,
                        VirtualKeyAllowedAlias.alias_id == ModelAlias.id,
                    )
                    .where(
                        VirtualKeyAllowedAlias.key_id == key.id,
                        ModelAlias.is_active.is_(True),
                    )
                )
            ).all()
            return VirtualKeyRecord(
                id=key.id,
                tenant_name=key.tenant_name,
                rpm_limit=key.rpm_limit,
                monthly_budget_usd=key.monthly_budget_usd,
                is_active=key.is_active,
                expires_at=key.expires_at,
                allowed_aliases=frozenset(aliases),
            )

    async def record_security_event(
        self,
        *,
        event_type: str,
        key_fingerprint: str | None,
        request_id: str,
        client_ip_hash: str | None,
    ) -> None:
        async with self._database.session() as session, session.begin():
            session.add(
                SecurityEvent(
                    event_type=event_type,
                    presented_key_fingerprint=key_fingerprint,
                    request_id=request_id,
                    client_ip_hash=client_ip_hash,
                    details={},
                )
            )


class PostgresAccountingRepository:
    """Keep admission and settlement locks inside short database transactions."""

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def get_pricing(
        self, provider_id: UUID, model: str, effective_at: datetime
    ) -> Pricing | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(ModelPricing)
                .where(
                    ModelPricing.provider_id == provider_id,
                    ModelPricing.model == model,
                    ModelPricing.effective_from <= effective_at,
                    (ModelPricing.effective_until.is_(None))
                    | (ModelPricing.effective_until > effective_at),
                )
                .order_by(ModelPricing.effective_from.desc())
                .limit(1)
            )
            if row is None:
                return None
            return Pricing(
                input_cost_per_1k=row.input_cost_per_1k,
                output_cost_per_1k=row.output_cost_per_1k,
            )

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
        async with self._database.session() as session, session.begin():
            counter = await session.scalar(
                select(AdmissionCounter)
                .where(AdmissionCounter.key_id == tenant.key_id)
                .with_for_update()
            )
            if counter is None:
                raise RuntimeError("admission counter is missing for authenticated key")

            if now >= counter.rpm_window_start + timedelta(minutes=1):
                counter.rpm_window_start = now
                counter.rpm_count = 0

            current_month = now.strftime("%Y-%m")
            if counter.budget_month != current_month:
                counter.budget_month = current_month
                counter.spent_usd = Decimal(0)
                counter.reserved_usd = Decimal(0)

            if counter.rpm_count >= tenant.rpm_limit:
                raise RateLimitExceededError("Requests per minute limit exceeded")
            projected = counter.spent_usd + counter.reserved_usd + reserved_usd
            if projected > tenant.monthly_budget_usd:
                raise BudgetExceededError("Monthly budget would be exceeded")

            request_log_id = uuid4()
            reservation_id = uuid4()
            counter.rpm_count += 1
            counter.reserved_usd += reserved_usd
            counter.updated_at = now
            session.add(
                RequestLog(
                    id=request_log_id,
                    key_id=tenant.key_id,
                    request_alias=alias,
                    status="in_progress",
                    request_fingerprint=request_fingerprint,
                    streamed=streamed,
                )
            )
            # The mappings intentionally have no ORM relationships; flush the parent
            # explicitly so PostgreSQL sees it before the reservation foreign key.
            await session.flush()
            session.add(
                BudgetReservation(
                    id=reservation_id,
                    key_id=tenant.key_id,
                    request_log_id=request_log_id,
                    reserved_usd=reserved_usd,
                    status="pending",
                )
            )
            return Admission(request_log_id, reservation_id, reserved_usd)

    async def settle(
        self,
        reservation_id: UUID,
        actual_cost_usd: Decimal,
        now: datetime,
        request_status: str = "success",
    ) -> Settlement:
        async with self._database.session() as session, session.begin():
            reservation = await session.scalar(
                select(BudgetReservation)
                .where(BudgetReservation.id == reservation_id)
                .with_for_update()
            )
            if reservation is None:
                raise LookupError("budget reservation not found")
            if reservation.status != "pending":
                return Settlement(
                    reservation.id,
                    reservation.status,
                    reservation.settled_usd,
                    False,
                )

            counter = await session.scalar(
                select(AdmissionCounter)
                .where(AdmissionCounter.key_id == reservation.key_id)
                .with_for_update()
            )
            if counter is None:
                raise RuntimeError("admission counter is missing for reservation")

            if counter.budget_month == reservation.created_at.strftime("%Y-%m"):
                counter.reserved_usd -= reservation.reserved_usd
                counter.spent_usd += actual_cost_usd
                counter.updated_at = now

            reservation.status = "settled"
            reservation.settled_usd = actual_cost_usd
            reservation.settled_at = now
            reservation.updated_at = now
            request_log = await session.get(RequestLog, reservation.request_log_id)
            if request_log is not None:
                request_log.status = request_status
                request_log.cost_usd = actual_cost_usd
                request_log.completed_at = now
            return Settlement(reservation.id, "settled", actual_cost_usd, True)

    async def release(
        self,
        reservation_id: UUID,
        now: datetime,
        reason: str,
        request_status: str = "cancelled",
        error_code: str | None = "reservation_released",
    ) -> Settlement:
        async with self._database.session() as session, session.begin():
            reservation = await session.scalar(
                select(BudgetReservation)
                .where(BudgetReservation.id == reservation_id)
                .with_for_update()
            )
            if reservation is None:
                raise LookupError("budget reservation not found")
            if reservation.status != "pending":
                return Settlement(
                    reservation.id,
                    reservation.status,
                    reservation.settled_usd,
                    False,
                )

            counter = await session.scalar(
                select(AdmissionCounter)
                .where(AdmissionCounter.key_id == reservation.key_id)
                .with_for_update()
            )
            if counter is None:
                raise RuntimeError("admission counter is missing for reservation")
            if counter.budget_month == reservation.created_at.strftime("%Y-%m"):
                counter.reserved_usd -= reservation.reserved_usd
                counter.updated_at = now

            reservation.status = "cancelled"
            reservation.settled_usd = Decimal(0)
            reservation.settled_at = now
            reservation.updated_at = now
            request_log = await session.get(RequestLog, reservation.request_log_id)
            if request_log is not None:
                request_log.status = request_status
                request_log.cost_usd = Decimal(0)
                request_log.error_code = error_code
                request_log.error_message = reason if error_code else None
                request_log.completed_at = now
            return Settlement(reservation.id, "cancelled", Decimal(0), True)


class PostgresProviderRepository:
    """Resolve configured provider chains and persist execution telemetry."""

    def __init__(self, database: DatabaseManager, metrics: MetricsRegistry | None = None) -> None:
        self._database = database
        self._metrics = metrics

    async def resolve_targets(self, alias: str, effective_at: datetime) -> list[ProviderTarget]:
        async with self._database.session() as session:
            model_alias = await session.scalar(
                select(ModelAlias).where(
                    ModelAlias.alias == alias,
                    ModelAlias.is_active.is_(True),
                )
            )
            if model_alias is None:
                raise AliasNotFoundError(f"Model alias {alias!r} was not found")
            if model_alias.tier == "auto":
                raise RoutingNotImplementedError(
                    "The auto routing policy is implemented in Phase 4; use fast or smart"
                )
            if model_alias.primary_provider_id is None or model_alias.primary_model is None:
                raise RuntimeError("physical alias has no primary provider target")

            target_specs: list[tuple[UUID, str]] = [
                (model_alias.primary_provider_id, model_alias.primary_model)
            ]
            fallbacks = (
                await session.scalars(
                    select(FallbackTarget)
                    .where(FallbackTarget.alias_id == model_alias.id)
                    .order_by(FallbackTarget.priority)
                )
            ).all()
            target_specs.extend((item.provider_id, item.model) for item in fallbacks)

            targets: list[ProviderTarget] = []
            for provider_id, model in target_specs:
                provider = await session.get(Provider, provider_id)
                if provider is None or not provider.is_active:
                    continue
                pricing = await session.scalar(
                    select(ModelPricing)
                    .where(
                        ModelPricing.provider_id == provider_id,
                        ModelPricing.model == model,
                        ModelPricing.effective_from <= effective_at,
                        (ModelPricing.effective_until.is_(None))
                        | (ModelPricing.effective_until > effective_at),
                    )
                    .order_by(ModelPricing.effective_from.desc())
                    .limit(1)
                )
                if pricing is None:
                    raise PricingNotFoundError(
                        f"No active pricing exists for provider {provider.name!r}, model {model!r}"
                    )
                targets.append(
                    ProviderTarget(
                        provider_id=provider.id,
                        provider_name=provider.name,
                        base_url=provider.base_url,
                        api_key_env_var=provider.api_key_env_var,
                        timeout_ms=provider.timeout_ms,
                        model=model,
                        pricing=Pricing(
                            pricing.input_cost_per_1k,
                            pricing.output_cost_per_1k,
                        ),
                    )
                )
            if not targets:
                raise AliasNotFoundError(f"No active targets exist for alias {alias!r}")
            return targets

    async def record_attempt(self, request_log_id: UUID, result: ProviderAttemptResult) -> None:
        latency_ms = max(0, int((result.ended_at - result.started_at).total_seconds() * 1000))
        async with self._database.session() as session, session.begin():
            session.add(
                ProviderAttempt(
                    request_log_id=request_log_id,
                    attempt_number=result.attempt_number,
                    retry_number=result.retry_number,
                    provider_id=result.target.provider_id,
                    model=result.target.model,
                    outcome=result.outcome,
                    backoff_ms=result.backoff_ms,
                    prompt_tokens=result.usage.prompt_tokens if result.usage else None,
                    completion_tokens=result.usage.completion_tokens if result.usage else None,
                    cost_usd=result.cost_usd,
                    started_at=result.started_at,
                    ended_at=result.ended_at,
                    latency_ms=latency_ms,
                    http_status=result.http_status,
                    error_code=result.error_code,
                    sanitized_error=result.sanitized_error,
                )
            )
        if self._metrics is not None:
            self._metrics.increment(
                "provider_attempts_total",
                provider=result.target.provider_name,
                outcome=result.outcome,
            )

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
    ) -> None:
        async with self._database.session() as session, session.begin():
            request_log = await session.get(RequestLog, request_log_id)
            if request_log is None:
                raise LookupError("request log not found")
            if request_log.resolved_alias is None:
                request_log.resolved_alias = request_log.request_alias
            request_log.provider_id = target.provider_id
            request_log.model = target.model
            request_log.fallback_triggered = fallback_triggered
            request_log.provider_latency_ms = latency_ms
            if usage is not None:
                request_log.prompt_tokens = usage.prompt_tokens
                request_log.completion_tokens = usage.completion_tokens
                request_log.total_tokens = usage.total_tokens
            if status is not None:
                request_log.status = status
            request_log.error_code = error_code
            request_log.error_message = error_message

    async def update_routing_decision(
        self,
        request_log_id: UUID,
        resolved_alias: str,
        decision: RoutingDecision,
    ) -> None:
        async with self._database.session() as session, session.begin():
            request_log = await session.get(RequestLog, request_log_id)
            if request_log is None:
                raise LookupError("request log not found")
            request_log.resolved_alias = resolved_alias
            request_log.auto_routed = decision.auto_routed
            request_log.routing_tier = decision.tier
            request_log.routing_reason = decision.reason_code
            request_log.routing_confidence = decision.confidence
            request_log.classifier_version = decision.classifier_version
