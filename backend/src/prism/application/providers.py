"""Visible, bounded retry and failover coordination."""

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from uuid import UUID

from prism.domain.accounting import money
from prism.domain.errors import ProviderTimeoutError, ProviderUnavailableError
from prism.domain.providers import (
    ChatCompletionRequest,
    ProviderAttemptResult,
    ProviderCallError,
    ProviderResponse,
    ProviderStream,
    ProviderTarget,
    StreamEvent,
    TokenUsage,
)
from prism.ports.providers import ProviderAdapter
from prism.ports.repositories import ProviderRepository


@dataclass(frozen=True, slots=True)
class CompletionExecution:
    target: ProviderTarget
    response: ProviderResponse
    cost_usd: Decimal
    fallback_triggered: bool
    latency_ms: int


@dataclass(slots=True)
class PreparedProviderStream:
    target: ProviderTarget
    stream: ProviderStream
    buffered_events: list[StreamEvent]
    attempt_number: int
    retry_number: int
    started_at: datetime
    fallback_triggered: bool
    latency_started: float


class ProviderCoordinator:
    def __init__(
        self,
        adapter: ProviderAdapter,
        repository: ProviderRepository,
        *,
        max_retries: int,
        backoff_base_ms: int,
    ) -> None:
        self._adapter = adapter
        self._repository = repository
        self._max_retries = max_retries
        self._backoff_base_ms = backoff_base_ms

    def _backoff_ms(self, retry_number: int) -> int:
        base = self._backoff_base_ms * (2 ** max(0, retry_number - 1))
        return int(base * random.uniform(0.8, 1.2))  # noqa: S311

    async def _record_failure(
        self,
        request_log_id: UUID,
        target: ProviderTarget,
        attempt_number: int,
        retry_number: int,
        started_at: datetime,
        error: ProviderCallError,
        backoff_ms: int | None,
    ) -> None:
        await self._repository.record_attempt(
            request_log_id,
            ProviderAttemptResult(
                attempt_number=attempt_number,
                retry_number=retry_number,
                target=target,
                outcome=error.outcome,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                backoff_ms=backoff_ms,
                http_status=error.http_status,
                error_code=error.code,
                sanitized_error=str(error)[:500],
            ),
        )

    async def complete(
        self,
        request_log_id: UUID,
        targets: list[ProviderTarget],
        request: ChatCompletionRequest,
    ) -> CompletionExecution:
        overall_started = monotonic()
        last_error: ProviderCallError | None = None
        for attempt_number, target in enumerate(targets, start=1):
            for retry_number in range(self._max_retries + 1):
                started_at = datetime.now(UTC)
                try:
                    response = await self._adapter.complete(target, request)
                except ProviderCallError as error:
                    last_error = error
                    will_retry = error.retryable and retry_number < self._max_retries
                    backoff_ms = self._backoff_ms(retry_number + 1) if will_retry else None
                    await self._record_failure(
                        request_log_id,
                        target,
                        attempt_number,
                        retry_number,
                        started_at,
                        error,
                        backoff_ms,
                    )
                    if will_retry:
                        assert backoff_ms is not None
                        await asyncio.sleep(backoff_ms / 1000)
                        continue
                    if not error.retryable:
                        raise ProviderUnavailableError(str(error)) from error
                    break

                cost = target.pricing.estimate(
                    response.usage.prompt_tokens, response.usage.completion_tokens
                )
                await self._repository.record_attempt(
                    request_log_id,
                    ProviderAttemptResult(
                        attempt_number=attempt_number,
                        retry_number=retry_number,
                        target=target,
                        outcome="success",
                        started_at=started_at,
                        ended_at=datetime.now(UTC),
                        usage=response.usage,
                        cost_usd=cost,
                        http_status=200,
                    ),
                )
                return CompletionExecution(
                    target=target,
                    response=response,
                    cost_usd=cost,
                    fallback_triggered=attempt_number > 1,
                    latency_ms=max(0, int((monotonic() - overall_started) * 1000)),
                )
        if last_error is not None and last_error.outcome == "timeout":
            raise ProviderTimeoutError("All provider attempts timed out") from last_error
        raise ProviderUnavailableError("All configured providers are unavailable") from last_error

    async def prepare_stream(
        self,
        request_log_id: UUID,
        targets: list[ProviderTarget],
        request: ChatCompletionRequest,
    ) -> PreparedProviderStream:
        latency_started = monotonic()
        last_error: ProviderCallError | None = None
        for attempt_number, target in enumerate(targets, start=1):
            for retry_number in range(self._max_retries + 1):
                started_at = datetime.now(UTC)
                stream: ProviderStream | None = None
                try:
                    stream = await self._adapter.stream(target, request)
                    buffered: list[StreamEvent] = []
                    async for event in stream.events:
                        buffered.append(event)
                        if event.content_bearing:
                            return PreparedProviderStream(
                                target=target,
                                stream=stream,
                                buffered_events=buffered,
                                attempt_number=attempt_number,
                                retry_number=retry_number,
                                started_at=started_at,
                                fallback_triggered=attempt_number > 1,
                                latency_started=latency_started,
                            )
                    return PreparedProviderStream(
                        target=target,
                        stream=stream,
                        buffered_events=buffered,
                        attempt_number=attempt_number,
                        retry_number=retry_number,
                        started_at=started_at,
                        fallback_triggered=attempt_number > 1,
                        latency_started=latency_started,
                    )
                except ProviderCallError as error:
                    last_error = error
                    if stream is not None:
                        await stream.close()
                    will_retry = error.retryable and retry_number < self._max_retries
                    backoff_ms = self._backoff_ms(retry_number + 1) if will_retry else None
                    await self._record_failure(
                        request_log_id,
                        target,
                        attempt_number,
                        retry_number,
                        started_at,
                        error,
                        backoff_ms,
                    )
                    if will_retry:
                        assert backoff_ms is not None
                        await asyncio.sleep(backoff_ms / 1000)
                        continue
                    if not error.retryable:
                        raise ProviderUnavailableError(str(error)) from error
                    break
        if last_error is not None and last_error.outcome == "timeout":
            raise ProviderTimeoutError("All provider stream attempts timed out") from last_error
        raise ProviderUnavailableError(
            "All configured provider streams are unavailable"
        ) from last_error

    async def record_stream_terminal(
        self,
        request_log_id: UUID,
        prepared: PreparedProviderStream,
        *,
        outcome: str,
        usage: TokenUsage | None = None,
        cost_usd: Decimal | None = None,
        error_code: str | None = None,
        sanitized_error: str | None = None,
    ) -> None:
        await self._repository.record_attempt(
            request_log_id,
            ProviderAttemptResult(
                attempt_number=prepared.attempt_number,
                retry_number=prepared.retry_number,
                target=prepared.target,
                outcome=outcome,
                started_at=prepared.started_at,
                ended_at=datetime.now(UTC),
                usage=usage,
                cost_usd=money(cost_usd) if cost_usd is not None else None,
                http_status=200,
                error_code=error_code,
                sanitized_error=sanitized_error,
            ),
        )
