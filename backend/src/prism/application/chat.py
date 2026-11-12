"""End-to-end chat completion orchestration."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic

import structlog

from prism.application.accounting import AccountingService
from prism.application.cache import SemanticCacheService
from prism.application.providers import (
    CompletionExecution,
    PreparedProviderStream,
    ProviderCoordinator,
)
from prism.application.routing import RoutingService, RuleBasedRoutingClassifier
from prism.domain.accounting import Admission, money
from prism.domain.providers import (
    ChatCompletionRequest,
    ProviderTarget,
    TokenUsage,
)
from prism.domain.routing import RoutingDecision
from prism.domain.security import TenantContext
from prism.ports.repositories import ProviderRepository

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChatResult:
    body: dict[str, object]
    provider: str
    fallback_triggered: bool
    cost_usd: Decimal
    request_id: str
    cache_hit: bool = False


@dataclass(slots=True)
class ChatStreamResult:
    events: AsyncIterator[bytes]
    provider: str
    fallback_triggered: bool
    reserved_usd: Decimal
    request_id: str
    cache_hit: bool = False


class ChatService:
    def __init__(
        self,
        accounting: AccountingService,
        provider_repository: ProviderRepository,
        coordinator: ProviderCoordinator,
        *,
        max_retries: int,
        routing: RoutingService | None = None,
        cache: SemanticCacheService | None = None,
    ) -> None:
        self._accounting = accounting
        self._provider_repository = provider_repository
        self._coordinator = coordinator
        self._max_retries = max_retries
        self._routing = routing or RoutingService(RuleBasedRoutingClassifier())
        self._cache = cache

    @staticmethod
    def _prompt_tokens(request: ChatCompletionRequest) -> int:
        return sum(max(1, (len(message.content) + 3) // 4) + 4 for message in request.messages)

    def _estimate(self, targets: list[ProviderTarget], request: ChatCompletionRequest) -> Decimal:
        prompt_tokens = self._prompt_tokens(request)
        per_target = [
            target.pricing.estimate(prompt_tokens, request.max_tokens) for target in targets
        ]
        return money(max(per_target) * Decimal(self._max_retries + 1))

    @staticmethod
    def _fingerprint(request: ChatCompletionRequest) -> str:
        canonical = request.model_dump(mode="json")
        canonical["stream"] = False
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    async def _admit(
        self, tenant: TenantContext, request: ChatCompletionRequest
    ) -> tuple[list[ProviderTarget], Admission, RoutingDecision]:
        prompt = "\n".join(message.content for message in request.messages)
        decision = self._routing.resolve(request.model, prompt, tenant)
        now = datetime.now(UTC)
        targets = await self._provider_repository.resolve_targets(decision.tier, now)
        reserved = self._estimate(targets, request)
        admission = await self._accounting.admit(
            tenant=tenant,
            alias=request.model,
            reserved_usd=reserved,
            request_fingerprint=self._fingerprint(request),
            streamed=request.stream,
            now=now,
        )
        await self._provider_repository.update_routing_decision(
            admission.request_log_id, decision.tier, decision
        )
        return targets, admission, decision

    async def complete(self, tenant: TenantContext, request: ChatCompletionRequest) -> ChatResult:
        targets, admission, decision = await self._admit(tenant, request)
        cache_query = None
        if self._cache is not None:
            try:
                hit, cache_query = await self._cache.lookup(
                    tenant, request, decision.tier, datetime.now(UTC)
                )
            except Exception as error:
                logger.warning("cache_lookup_failed", error_type=type(error).__name__)
                hit = None
            if hit is not None:
                await self._cache.mark_hit(
                    admission.request_log_id,
                    hit,
                    decision.tier,
                    False,
                    datetime.now(UTC),
                )
                await self._accounting.release(
                    admission.reservation_id,
                    datetime.now(UTC),
                    "semantic cache hit",
                    "success",
                    None,
                )
                body = dict(hit.response_body)
                body["model"] = request.model
                return ChatResult(
                    body=body,
                    provider="cache",
                    fallback_triggered=False,
                    cost_usd=Decimal(0),
                    request_id=str(admission.request_log_id),
                    cache_hit=True,
                )
        try:
            execution: CompletionExecution = await self._coordinator.complete(
                admission.request_log_id, targets, request
            )
            execution.response.body["model"] = request.model
            await self._provider_repository.update_request_execution(
                request_log_id=admission.request_log_id,
                target=execution.target,
                usage=execution.response.usage,
                fallback_triggered=execution.fallback_triggered,
                latency_ms=execution.latency_ms,
            )
            await self._accounting.settle(
                admission.reservation_id, execution.cost_usd, datetime.now(UTC)
            )
            if self._cache is not None:
                try:
                    await self._cache.store(
                        tenant,
                        request,
                        decision.tier,
                        execution.response,
                        execution.target.provider_name,
                        execution.cost_usd,
                        datetime.now(UTC),
                        cache_query,
                    )
                except Exception as error:
                    logger.warning("cache_store_failed", error_type=type(error).__name__)
            return ChatResult(
                body=execution.response.body,
                provider=execution.target.provider_name,
                fallback_triggered=execution.fallback_triggered,
                cost_usd=execution.cost_usd,
                request_id=str(admission.request_log_id),
            )
        except Exception:
            await self._accounting.release(
                admission.reservation_id, datetime.now(UTC), "provider execution failed"
            )
            raise

    async def stream(
        self, tenant: TenantContext, request: ChatCompletionRequest
    ) -> ChatStreamResult:
        targets, admission, decision = await self._admit(tenant, request)
        if self._cache is not None:
            try:
                hit, _ = await self._cache.lookup(tenant, request, decision.tier, datetime.now(UTC))
            except Exception as error:
                logger.warning("cache_lookup_failed", error_type=type(error).__name__)
                hit = None
            if hit is not None:
                await self._cache.mark_hit(
                    admission.request_log_id,
                    hit,
                    decision.tier,
                    True,
                    datetime.now(UTC),
                )
                await self._accounting.release(
                    admission.reservation_id,
                    datetime.now(UTC),
                    "semantic cache hit",
                    "success",
                    None,
                )
                return ChatStreamResult(
                    events=self._cache.cached_sse(hit, request.model),
                    provider="cache",
                    fallback_triggered=False,
                    reserved_usd=Decimal(0),
                    request_id=str(admission.request_log_id),
                    cache_hit=True,
                )
        try:
            prepared = await self._coordinator.prepare_stream(
                admission.request_log_id, targets, request
            )
        except Exception:
            await self._accounting.release(
                admission.reservation_id, datetime.now(UTC), "provider stream failed before commit"
            )
            raise

        async def generate() -> AsyncIterator[bytes]:
            usage: TokenUsage | None = None
            committed = any(event.content_bearing for event in prepared.buffered_events)
            try:
                for event in prepared.buffered_events:
                    usage = event.usage or usage
                    yield f"data: {json.dumps(event.data, separators=(',', ':'))}\n\n".encode()
                async for event in prepared.stream.events:
                    usage = event.usage or usage
                    committed = committed or event.content_bearing
                    yield f"data: {json.dumps(event.data, separators=(',', ':'))}\n\n".encode()

                actual_cost = (
                    prepared.target.pricing.estimate(usage.prompt_tokens, usage.completion_tokens)
                    if usage is not None
                    else admission.reserved_usd
                )
                await self._coordinator.record_stream_terminal(
                    admission.request_log_id,
                    prepared,
                    outcome="success",
                    usage=usage,
                    cost_usd=actual_cost,
                )
                await self._provider_repository.update_request_execution(
                    request_log_id=admission.request_log_id,
                    target=prepared.target,
                    usage=usage,
                    fallback_triggered=prepared.fallback_triggered,
                    latency_ms=max(0, int((monotonic() - prepared.latency_started) * 1000)),
                )
                await self._accounting.settle(
                    admission.reservation_id, actual_cost, datetime.now(UTC)
                )
                yield b"data: [DONE]\n\n"
            except asyncio.CancelledError:
                await prepared.stream.close()
                await self._stream_failure(
                    admission, prepared, usage, committed, "cancelled", "client_disconnected"
                )
                raise
            except Exception as error:
                await prepared.stream.close()
                await self._stream_failure(
                    admission,
                    prepared,
                    usage,
                    committed,
                    "partial_stream_error" if committed else "transient_error",
                    getattr(error, "code", "provider_stream_error"),
                )
                return

        return ChatStreamResult(
            events=generate(),
            provider=prepared.target.provider_name,
            fallback_triggered=prepared.fallback_triggered,
            reserved_usd=admission.reserved_usd,
            request_id=str(admission.request_log_id),
        )

    async def _stream_failure(
        self,
        admission: Admission,
        prepared: PreparedProviderStream,
        usage: TokenUsage | None,
        committed: bool,
        outcome: str,
        error_code: str,
    ) -> None:
        cost = admission.reserved_usd if committed else Decimal(0)
        await self._coordinator.record_stream_terminal(
            admission.request_log_id,
            prepared,
            outcome=outcome,
            usage=usage,
            cost_usd=cost,
            error_code=error_code,
            sanitized_error="provider stream ended before clean completion",
        )
        await self._provider_repository.update_request_execution(
            request_log_id=admission.request_log_id,
            target=prepared.target,
            usage=usage,
            fallback_triggered=prepared.fallback_triggered,
            latency_ms=max(0, int((monotonic() - prepared.latency_started) * 1000)),
            status="partial_stream_error" if committed else "error",
            error_code=error_code,
            error_message="provider stream ended before clean completion",
        )
        if committed:
            await self._accounting.settle(
                admission.reservation_id,
                cost,
                datetime.now(UTC),
                "partial_stream_error",
            )
        else:
            await self._accounting.release(admission.reservation_id, datetime.now(UTC), error_code)
