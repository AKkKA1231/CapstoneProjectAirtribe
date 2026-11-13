"""Canonical scoping, eligibility, lookup, and storage for semantic cache."""

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from prism.domain.cache import CacheHit, CacheQuery
from prism.domain.providers import ChatCompletionRequest, ProviderResponse
from prism.domain.security import TenantContext
from prism.ports.cache import CacheRepository
from prism.ports.embeddings import EmbeddingProvider


class SemanticCacheService:
    def __init__(
        self,
        repository: CacheRepository,
        embedder: EmbeddingProvider,
        *,
        enabled: bool,
        ttl_seconds: int,
        similarity_threshold: float,
        schema_version: int,
        maximum_temperature: float,
    ) -> None:
        if embedder.dimensions != 1536:
            raise ValueError("cache embedder must produce exactly 1536 dimensions")
        self._repository = repository
        self._embedder = embedder
        self._enabled = enabled
        self._ttl_seconds = ttl_seconds
        self._similarity_threshold = similarity_threshold
        self._schema_version = schema_version
        self._maximum_temperature = maximum_temperature

    def eligible(self, request: ChatCompletionRequest) -> bool:
        return self._enabled and request.temperature <= self._maximum_temperature

    @staticmethod
    def _response_cacheable(response: ProviderResponse) -> bool:
        """Exclude tool/function-call responses until replay semantics are defined."""

        for choice in response.body.get("choices", []):
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason") in {"tool_calls", "function_call"}:
                return False
            message = choice.get("message")
            if isinstance(message, dict) and (
                message.get("tool_calls") is not None or message.get("function_call") is not None
            ):
                return False
        return True

    def _canonical(
        self, request: ChatCompletionRequest, resolved_alias: str
    ) -> tuple[str, str, str]:
        body = request.model_dump(mode="json")
        body["stream"] = False
        messages = body["messages"]
        last_user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index]["role"] == "user"
            ),
            None,
        )
        if last_user_index is None:
            prompt_text = "\n".join(message["content"] for message in messages)
        else:
            prompt_text = messages[last_user_index]["content"]
            messages[last_user_index] = {"role": "user", "content": "<semantic-query>"}
        body["resolved_alias"] = resolved_alias
        body["cache_contract"] = {
            "schema_version": self._schema_version,
            "embedding_model": self._embedder.model,
            "embedding_version": self._embedder.version,
        }
        scope_payload = json.dumps(body, sort_keys=True, separators=(",", ":"))

        exact = request.model_dump(mode="json")
        exact["stream"] = False
        exact["resolved_alias"] = resolved_alias
        fingerprint_payload = json.dumps(exact, sort_keys=True, separators=(",", ":"))
        return (
            prompt_text,
            hashlib.sha256(scope_payload.encode()).hexdigest(),
            hashlib.sha256(fingerprint_payload.encode()).hexdigest(),
        )

    async def mark_hit(
        self,
        request_log_id: UUID,
        hit: CacheHit,
        resolved_alias: str,
        streamed: bool,
        now: datetime,
    ) -> None:
        await self._repository.mark_request_hit(
            request_log_id=request_log_id,
            hit=hit,
            resolved_alias=resolved_alias,
            streamed=streamed,
            now=now,
        )

    @staticmethod
    async def cached_sse(hit: CacheHit, requested_model: str) -> AsyncIterator[bytes]:
        body = hit.response_body
        response_id = str(body.get("id", f"chatcmpl-cache-{hit.entry_id.hex}"))
        choices = body.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        content = str(message.get("content", ""))
        common = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "model": requested_model,
        }
        role = {**common, "choices": [{"index": 0, "delta": {"role": "assistant"}}]}
        content_chunk = {
            **common,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
        }
        final = {
            **common,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": hit.usage.model_dump() if hit.usage else None,
        }
        for chunk in (role, content_chunk, final):
            yield f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode()
        yield b"data: [DONE]\n\n"

    def query(self, request: ChatCompletionRequest, resolved_alias: str) -> CacheQuery:
        prompt_text, scope_hash, fingerprint = self._canonical(request, resolved_alias)
        embedding = self._embedder.embed(prompt_text)
        if len(embedding) != self._embedder.dimensions:
            raise ValueError("embedding adapter returned an invalid dimension")
        return CacheQuery(scope_hash, fingerprint, prompt_text, embedding)

    async def lookup(
        self,
        tenant: TenantContext,
        request: ChatCompletionRequest,
        resolved_alias: str,
        now: datetime,
    ) -> tuple[CacheHit | None, CacheQuery | None]:
        if not self.eligible(request):
            return None, None
        query = self.query(request, resolved_alias)
        hit = await self._repository.lookup(
            key_id=tenant.key_id,
            query=query,
            now=now,
            embedding_model=self._embedder.model,
            embedding_version=self._embedder.version,
            schema_version=self._schema_version,
            similarity_threshold=self._similarity_threshold,
        )
        return hit, query

    async def store(
        self,
        tenant: TenantContext,
        request: ChatCompletionRequest,
        resolved_alias: str,
        response: ProviderResponse,
        provider: str,
        cost_usd: Decimal,
        now: datetime,
        query: CacheQuery | None = None,
    ) -> None:
        if not self.eligible(request) or not self._response_cacheable(response):
            return
        resolved_query = query or self.query(request, resolved_alias)
        await self._repository.store(
            key_id=tenant.key_id,
            query=resolved_query,
            response_body=response.body,
            model=resolved_alias,
            provider=provider,
            usage=response.usage,
            cost_usd=cost_usd,
            now=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            embedding_model=self._embedder.model,
            embedding_version=self._embedder.version,
            schema_version=self._schema_version,
            similarity_threshold=self._similarity_threshold,
        )
