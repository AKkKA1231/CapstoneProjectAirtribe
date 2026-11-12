"""Provider adapter, retry, failover, and stream-commitment tests."""

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from prism.adapters.providers.openai_compatible import OpenAICompatibleProviderAdapter
from prism.application.providers import ProviderCoordinator
from prism.domain.accounting import Pricing
from prism.domain.errors import ProviderUnavailableError
from prism.domain.providers import (
    ChatCompletionRequest,
    ChatMessage,
    ProviderAttemptResult,
    ProviderCallError,
    ProviderResponse,
    ProviderStream,
    ProviderTarget,
    StreamEvent,
    TokenUsage,
)


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class FakeProviderRepository:
    def __init__(self) -> None:
        self.attempts: list[ProviderAttemptResult] = []

    async def record_attempt(self, request_log_id: object, result: ProviderAttemptResult) -> None:
        self.attempts.append(result)

    async def resolve_targets(self, alias: str, effective_at: datetime):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def update_request_execution(self, **_: object) -> None:
        return None


class SequencedAdapter:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def complete(
        self, target: ProviderTarget, request: ChatCompletionRequest
    ) -> ProviderResponse:
        self.calls.append(target.provider_name)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[no-any-return]

    async def stream(
        self, target: ProviderTarget, request: ChatCompletionRequest
    ) -> ProviderStream:
        self.calls.append(target.provider_name)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[no-any-return]


def target(name: str) -> ProviderTarget:
    return ProviderTarget(
        provider_id=uuid4(),
        provider_name=name,
        base_url=f"https://{name}.test/v1",
        api_key_env_var=None,
        timeout_ms=1000,
        model="mock-v1",
        pricing=Pricing(Decimal("0.001"), Decimal("0.002")),
    )


def request(*, stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="hello")],
        stream=stream,
    )


def response() -> ProviderResponse:
    usage = TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    return ProviderResponse(
        body={"id": "chatcmpl-1", "choices": [], "usage": usage.model_dump()},
        usage=usage,
    )


@pytest.mark.asyncio
async def test_http_adapter_normalizes_non_streaming_usage() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "choices": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleProviderAdapter(client).complete(target("mock-a"), request())

    assert result.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_http_adapter_keeps_provider_secret_in_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groq = replace(target("groq"), api_key_env_var="GROQ_API_KEY")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as client:
        with pytest.raises(ProviderCallError) as missing:
            await OpenAICompatibleProviderAdapter(client).complete(groq, request())
    assert missing.value.code == "provider_configuration_error"

    monkeypatch.setenv("GROQ_API_KEY", "test-only-provider-secret")

    async def handler(provider_request: httpx.Request) -> httpx.Response:
        assert provider_request.headers["Authorization"] == "Bearer test-only-provider-secret"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-groq",
                "choices": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleProviderAdapter(client).complete(groq, request())
    assert result.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_http_adapter_classifies_retryable_and_fatal_errors() -> None:
    for status, retryable in [(429, True), (503, True), (400, False)]:

        async def handler(_: httpx.Request, response_status: int = status) -> httpx.Response:
            return httpx.Response(response_status, json={"error": "planned"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderCallError) as raised:
                await OpenAICompatibleProviderAdapter(client).complete(target("mock-a"), request())
        assert raised.value.retryable is retryable


@pytest.mark.asyncio
async def test_http_adapter_classifies_timeout_and_malformed_body() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("planned timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        with pytest.raises(ProviderCallError) as timeout_error:
            await OpenAICompatibleProviderAdapter(client).complete(target("mock-a"), request())
    assert timeout_error.value.outcome == "timeout"
    assert timeout_error.value.retryable is True

    async def malformed_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed_handler)) as client:
        with pytest.raises(ProviderCallError) as malformed_error:
            await OpenAICompatibleProviderAdapter(client).complete(target("mock-a"), request())
    assert malformed_error.value.code == "malformed_provider_response"
    assert malformed_error.value.retryable is True


@pytest.mark.asyncio
async def test_http_adapter_accepts_clean_sse_and_rejects_missing_done() -> None:
    content = {"choices": [{"delta": {"content": "hello"}}]}
    clean = [
        f"data: {json.dumps(content)}\n\n".encode(),
        (
            b'data: {"choices":[],"usage":{"prompt_tokens":1,'
            b'"completion_tokens":1,"total_tokens":2}}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]

    async def run(chunks: list[bytes]) -> list[StreamEvent]:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=BytesStream(chunks))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            stream = await OpenAICompatibleProviderAdapter(client).stream(
                target("mock-a"), request(stream=True)
            )
            return [event async for event in stream.events]

    events = await run(clean)
    assert events[0].content_bearing is True
    assert events[-1].usage is not None
    with pytest.raises(ProviderCallError, match="without a completion sentinel"):
        await run(clean[:-1])


@pytest.mark.asyncio
async def test_coordinator_retries_then_falls_back_and_records_every_call() -> None:
    transient = ProviderCallError(
        "planned",
        retryable=True,
        outcome="transient_error",
        http_status=503,
    )
    adapter = SequencedAdapter([transient, transient, response()])
    repository = FakeProviderRepository()
    coordinator = ProviderCoordinator(
        adapter,
        repository,
        max_retries=1,
        backoff_base_ms=0,  # type: ignore[arg-type]
    )

    result = await coordinator.complete(uuid4(), [target("mock-a"), target("mock-b")], request())

    assert result.target.provider_name == "mock-b"
    assert result.fallback_triggered is True
    assert adapter.calls == ["mock-a", "mock-a", "mock-b"]
    assert [attempt.outcome for attempt in repository.attempts] == [
        "transient_error",
        "transient_error",
        "success",
    ]


@pytest.mark.asyncio
async def test_fatal_provider_error_does_not_retry_or_fail_over() -> None:
    fatal = ProviderCallError("fatal", retryable=False, outcome="fatal_error", http_status=400)
    adapter = SequencedAdapter([fatal])
    repository = FakeProviderRepository()
    coordinator = ProviderCoordinator(
        adapter,
        repository,
        max_retries=2,
        backoff_base_ms=0,  # type: ignore[arg-type]
    )

    with pytest.raises(ProviderUnavailableError):
        await coordinator.complete(uuid4(), [target("mock-a"), target("mock-b")], request())
    assert adapter.calls == ["mock-a"]


@pytest.mark.asyncio
async def test_stream_can_fail_over_only_before_content_commitment() -> None:
    transient = ProviderCallError("planned", retryable=True, outcome="transient_error")

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent({"choices": [{"delta": {"content": "ok"}}]}, True)

    fallback_stream = ProviderStream(events=events(), close=lambda: None)
    adapter = SequencedAdapter([transient, fallback_stream])
    repository = FakeProviderRepository()
    coordinator = ProviderCoordinator(
        adapter,
        repository,
        max_retries=0,
        backoff_base_ms=0,  # type: ignore[arg-type]
    )

    prepared = await coordinator.prepare_stream(
        uuid4(), [target("mock-a"), target("mock-b")], request(stream=True)
    )

    assert prepared.target.provider_name == "mock-b"
    assert prepared.fallback_triggered is True
    assert prepared.buffered_events[0].content_bearing is True
