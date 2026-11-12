"""OpenAI-compatible HTTP adapter with explicit error classification."""

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import ValidationError

from prism.domain.providers import (
    ChatCompletionRequest,
    ProviderCallError,
    ProviderResponse,
    ProviderStream,
    ProviderTarget,
    StreamEvent,
    TokenUsage,
)


def _payload(
    target: ProviderTarget, request: ChatCompletionRequest, *, stream: bool
) -> dict[str, Any]:
    body = request.model_dump(exclude={"model"}, exclude_none=True)
    body["model"] = target.model
    body["stream"] = stream
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _headers(target: ProviderTarget) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if target.api_key_env_var:
        secret = os.getenv(target.api_key_env_var)
        if not secret:
            raise ProviderCallError(
                "provider credential is not configured",
                retryable=False,
                outcome="fatal_error",
                code="provider_configuration_error",
            )
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _http_error(response: httpx.Response) -> ProviderCallError:
    status = response.status_code
    retryable = status in {408, 429} or 500 <= status <= 599
    outcome = "rate_limited" if status == 429 else "transient_error" if retryable else "fatal_error"
    return ProviderCallError(
        f"provider returned HTTP {status}",
        retryable=retryable,
        outcome=outcome,
        http_status=status,
        code=f"provider_http_{status}",
    )


def _transport_error(error: Exception) -> ProviderCallError:
    if isinstance(error, httpx.TimeoutException):
        return ProviderCallError(
            "provider request timed out",
            retryable=True,
            outcome="timeout",
            code="provider_timeout",
        )
    return ProviderCallError(
        "provider connection failed",
        retryable=True,
        outcome="transient_error",
        code="provider_connection_error",
    )


class OpenAICompatibleProviderAdapter:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def complete(
        self, target: ProviderTarget, request: ChatCompletionRequest
    ) -> ProviderResponse:
        try:
            response = await self._client.post(
                f"{target.base_url.rstrip('/')}/chat/completions",
                headers=_headers(target),
                json=_payload(target, request, stream=False),
                timeout=target.timeout_ms / 1000,
            )
        except httpx.RequestError as error:
            raise _transport_error(error) from error
        if response.status_code >= 400:
            raise _http_error(response)
        try:
            body = response.json()
            usage = TokenUsage.model_validate(body["usage"])
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as error:
            raise ProviderCallError(
                "provider returned a malformed response",
                retryable=True,
                outcome="transient_error",
                http_status=response.status_code,
                code="malformed_provider_response",
            ) from error
        return ProviderResponse(body=body, usage=usage)

    async def stream(
        self, target: ProviderTarget, request: ChatCompletionRequest
    ) -> ProviderStream:
        http_request = self._client.build_request(
            "POST",
            f"{target.base_url.rstrip('/')}/chat/completions",
            headers=_headers(target),
            json=_payload(target, request, stream=True),
            timeout=target.timeout_ms / 1000,
        )
        try:
            response = await self._client.send(http_request, stream=True)
        except httpx.RequestError as error:
            raise _transport_error(error) from error
        if response.status_code >= 400:
            response_error = _http_error(response)
            await response.aclose()
            raise response_error

        async def events() -> AsyncIterator[StreamEvent]:
            saw_done = False
            try:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw_data = line[5:].strip()
                    if not raw_data:
                        continue
                    if raw_data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        data = json.loads(raw_data)
                        usage_data = data.get("usage")
                        usage = TokenUsage.model_validate(usage_data) if usage_data else None
                        choices = data.get("choices") or []
                        content_bearing = any(
                            bool((choice.get("delta") or {}).get("content")) for choice in choices
                        )
                    except (json.JSONDecodeError, TypeError, ValidationError) as error:
                        raise ProviderCallError(
                            "provider returned malformed SSE data",
                            retryable=True,
                            outcome="transient_error",
                            code="malformed_provider_stream",
                        ) from error
                    yield StreamEvent(data=data, content_bearing=content_bearing, usage=usage)
                if not saw_done:
                    raise ProviderCallError(
                        "provider stream ended without a completion sentinel",
                        retryable=True,
                        outcome="partial_stream_error",
                        code="incomplete_provider_stream",
                    )
            except httpx.RequestError as error:
                raise _transport_error(error) from error
            finally:
                await response.aclose()

        return ProviderStream(events=events(), close=response.aclose)
