"""Chat orchestration tests for settlement and stream terminal paths."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from prism.application.chat import ChatService
from prism.application.providers import CompletionExecution, PreparedProviderStream
from prism.domain.accounting import Admission, Pricing, Settlement
from prism.domain.errors import ProviderUnavailableError
from prism.domain.providers import (
    ChatCompletionRequest,
    ChatMessage,
    ProviderCallError,
    ProviderResponse,
    ProviderStream,
    ProviderTarget,
    StreamEvent,
    TokenUsage,
)
from prism.domain.security import TenantContext


def target() -> ProviderTarget:
    return ProviderTarget(
        provider_id=uuid4(),
        provider_name="mock-a",
        base_url="https://mock-a.test/v1",
        api_key_env_var=None,
        timeout_ms=1000,
        model="mock-fast-v1",
        pricing=Pricing(Decimal("0.001"), Decimal("0.002")),
    )


def request(*, stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="hello world")],
        stream=stream,
        max_tokens=20,
    )


class FakeAccounting:
    def __init__(self) -> None:
        self.admission = Admission(uuid4(), uuid4(), Decimal("0.1"))
        self.settled: list[tuple[Decimal, str]] = []
        self.released: list[str] = []

    async def admit(self, **_: object) -> Admission:
        return self.admission

    async def settle(
        self,
        reservation_id: object,
        cost: Decimal,
        now: datetime,
        request_status: str = "success",
    ) -> Settlement:
        self.settled.append((cost, request_status))
        return Settlement(self.admission.reservation_id, "settled", cost, True)

    async def release(
        self,
        reservation_id: object,
        now: datetime,
        reason: str,
        request_status: str = "cancelled",
        error_code: str | None = "reservation_released",
    ) -> Settlement:
        self.released.append(reason)
        return Settlement(self.admission.reservation_id, request_status, Decimal(0), True)


class FakeProviderRepository:
    def __init__(self, resolved_target: ProviderTarget) -> None:
        self.target = resolved_target
        self.updates: list[dict[str, object]] = []

    async def resolve_targets(self, alias: str, effective_at: datetime) -> list[ProviderTarget]:
        return [self.target]

    async def update_request_execution(self, **values: object) -> None:
        self.updates.append(values)

    async def update_routing_decision(
        self, request_log_id: object, resolved_alias: str, decision: object
    ) -> None:
        self.updates.append(
            {
                "request_log_id": request_log_id,
                "resolved_alias": resolved_alias,
                "decision": decision,
            }
        )

    async def record_attempt(self, request_log_id: object, result: object) -> None:
        return None


class FakeCoordinator:
    def __init__(
        self,
        resolved_target: ProviderTarget,
        prepared: PreparedProviderStream | None = None,
        prepare_error: Exception | None = None,
    ) -> None:
        self.target = resolved_target
        self.prepared = prepared
        self.prepare_error = prepare_error
        self.terminals: list[dict[str, object]] = []

    async def complete(
        self, request_log_id: object, targets: object, body: object
    ) -> CompletionExecution:
        usage = TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
        response = ProviderResponse(
            body={"id": "chatcmpl-1", "model": "physical", "usage": usage.model_dump()},
            usage=usage,
        )
        return CompletionExecution(self.target, response, Decimal("0.0000080000"), False, 5)

    async def prepare_stream(
        self, request_log_id: object, targets: object, body: object
    ) -> PreparedProviderStream:
        if self.prepare_error is not None:
            raise self.prepare_error
        assert self.prepared is not None
        return self.prepared

    async def record_stream_terminal(self, *args: object, **values: object) -> None:
        self.terminals.append(values)


def tenant() -> TenantContext:
    return TenantContext(uuid4(), "Tenant", 20, Decimal("10"), frozenset({"fast"}))


async def empty_events() -> AsyncIterator[StreamEvent]:
    if False:
        yield StreamEvent({}, False)


async def close_stream() -> None:
    return None


@pytest.mark.asyncio
async def test_non_streaming_completion_updates_and_settles_actual_cost() -> None:
    resolved_target = target()
    accounting = FakeAccounting()
    repository = FakeProviderRepository(resolved_target)
    coordinator = FakeCoordinator(resolved_target)
    service = ChatService(
        accounting,
        repository,
        coordinator,
        max_retries=1,  # type: ignore[arg-type]
    )

    result = await service.complete(tenant(), request())

    assert result.body["model"] == "fast"
    assert result.provider == "mock-a"
    assert accounting.settled == [(Decimal("0.0000080000"), "success")]
    assert accounting.released == []
    execution_update = next(
        update for update in repository.updates if "fallback_triggered" in update
    )
    assert execution_update["fallback_triggered"] is False


@pytest.mark.asyncio
async def test_clean_stream_emits_done_and_settles_reported_usage() -> None:
    resolved_target = target()
    usage = TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)

    async def remaining() -> AsyncIterator[StreamEvent]:
        yield StreamEvent({"choices": [], "usage": usage.model_dump()}, False, usage)

    prepared = PreparedProviderStream(
        target=resolved_target,
        stream=ProviderStream(remaining(), close_stream),
        buffered_events=[StreamEvent({"choices": [{"delta": {"content": "hi"}}]}, True)],
        attempt_number=1,
        retry_number=0,
        started_at=datetime.now(UTC),
        fallback_triggered=False,
        latency_started=0,
    )
    accounting = FakeAccounting()
    repository = FakeProviderRepository(resolved_target)
    coordinator = FakeCoordinator(resolved_target, prepared)
    service = ChatService(
        accounting,
        repository,
        coordinator,
        max_retries=0,  # type: ignore[arg-type]
    )

    result = await service.stream(tenant(), request(stream=True))
    frames = [frame async for frame in result.events]

    assert frames[-1] == b"data: [DONE]\n\n"
    assert accounting.settled == [(Decimal("0.0000080000"), "success")]
    assert coordinator.terminals[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_post_commit_stream_failure_never_emits_done_and_charges_reservation() -> None:
    resolved_target = target()

    async def failing() -> AsyncIterator[StreamEvent]:
        raise ProviderCallError(
            "connection lost",
            retryable=True,
            outcome="partial_stream_error",
            code="connection_lost",
        )
        yield StreamEvent({}, False)

    prepared = PreparedProviderStream(
        target=resolved_target,
        stream=ProviderStream(failing(), close_stream),
        buffered_events=[StreamEvent({"choices": [{"delta": {"content": "hi"}}]}, True)],
        attempt_number=1,
        retry_number=0,
        started_at=datetime.now(UTC),
        fallback_triggered=False,
        latency_started=0,
    )
    accounting = FakeAccounting()
    repository = FakeProviderRepository(resolved_target)
    coordinator = FakeCoordinator(resolved_target, prepared)
    service = ChatService(
        accounting,
        repository,
        coordinator,
        max_retries=0,  # type: ignore[arg-type]
    )

    result = await service.stream(tenant(), request(stream=True))
    frames = [frame async for frame in result.events]

    assert b"[DONE]" not in b"".join(frames)
    assert accounting.settled == [(Decimal("0.1000000000"), "partial_stream_error")]
    assert coordinator.terminals[0]["outcome"] == "partial_stream_error"
    assert repository.updates[-1]["status"] == "partial_stream_error"


@pytest.mark.asyncio
async def test_precommit_stream_failure_releases_reservation() -> None:
    resolved_target = target()
    accounting = FakeAccounting()
    repository = FakeProviderRepository(resolved_target)
    coordinator = FakeCoordinator(
        resolved_target,
        prepare_error=ProviderUnavailableError("unavailable"),
    )
    service = ChatService(
        accounting,
        repository,
        coordinator,
        max_retries=0,  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderUnavailableError):
        await service.stream(tenant(), request(stream=True))
    assert accounting.released == ["provider stream failed before commit"]
