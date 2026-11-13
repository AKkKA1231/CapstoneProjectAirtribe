"""OpenAI-compatible chat completions for JSON and SSE transports."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from prism.adapters.database.cache import PostgresCacheRepository
from prism.adapters.database.repositories import (
    PostgresAccountingRepository,
    PostgresProviderRepository,
)
from prism.adapters.embeddings.local import DeterministicHashEmbedding
from prism.adapters.providers.openai_compatible import OpenAICompatibleProviderAdapter
from prism.api.dependencies import (
    DatabaseDependency,
    MetricsDependency,
    ProviderHttpClientDependency,
    SettingsDependency,
    TenantDependency,
)
from prism.application.accounting import AccountingService
from prism.application.cache import SemanticCacheService
from prism.application.chat import ChatService
from prism.application.providers import ProviderCoordinator
from prism.domain.errors import DatabaseUnavailableError
from prism.domain.providers import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["chat"])


def _service(
    database: DatabaseDependency,
    http_client: ProviderHttpClientDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> ChatService:
    if database is None:
        raise DatabaseUnavailableError("The accounting database is unavailable")
    provider_repository = PostgresProviderRepository(database, metrics)
    coordinator = ProviderCoordinator(
        OpenAICompatibleProviderAdapter(http_client),
        provider_repository,
        max_retries=settings.provider_max_retries,
        backoff_base_ms=settings.provider_backoff_base_ms,
    )
    return ChatService(
        AccountingService(PostgresAccountingRepository(database), metrics),
        provider_repository,
        coordinator,
        max_retries=settings.provider_max_retries,
        cache=SemanticCacheService(
            PostgresCacheRepository(database),
            DeterministicHashEmbedding(),
            enabled=settings.cache_enabled,
            ttl_seconds=settings.cache_ttl_seconds,
            similarity_threshold=settings.cache_similarity_threshold,
            schema_version=settings.cache_schema_version,
            maximum_temperature=settings.cache_maximum_temperature,
        ),
    )


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionRequest,
    tenant: TenantDependency,
    database: DatabaseDependency,
    http_client: ProviderHttpClientDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> JSONResponse | StreamingResponse:
    service = _service(database, http_client, settings, metrics)
    if body.stream:
        stream_result = await service.stream(tenant, body)
        return StreamingResponse(
            stream_result.events,
            media_type="text/event-stream",
            headers={
                "x-prism-provider": stream_result.provider,
                "x-prism-cache": "hit" if stream_result.cache_hit else "miss",
                "x-prism-fallback": str(stream_result.fallback_triggered).lower(),
                "x-prism-cost-usd": str(stream_result.reserved_usd),
                "x-prism-request-id": stream_result.request_id,
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    result = await service.complete(tenant, body)
    return JSONResponse(
        result.body,
        headers={
            "x-prism-provider": result.provider,
            "x-prism-cache": "hit" if result.cache_hit else "miss",
            "x-prism-fallback": str(result.fallback_triggered).lower(),
            "x-prism-cost-usd": str(result.cost_usd),
            "x-prism-request-id": result.request_id,
        },
    )
