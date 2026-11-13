"""Shared FastAPI dependencies, including tenant and admin authentication."""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from prism.adapters.database.repositories import PostgresSecurityRepository
from prism.adapters.database.session import DatabaseManager
from prism.application.security import AuthenticationService, authenticate_admin
from prism.domain.errors import (
    DatabaseUnavailableError,
    MissingApiKeyError,
    PrismError,
)
from prism.domain.security import TenantContext
from prism.infrastructure.config import Settings
from prism.infrastructure.metrics import MetricsRegistry

bearer_scheme = HTTPBearer(auto_error=False)


async def get_app_settings(request: Request) -> Settings:
    """Return settings bound to the current application."""

    return request.app.state.settings  # type: ignore[no-any-return]


async def get_database(request: Request) -> DatabaseManager | None:
    """Return the database manager when the application lifespan has started."""

    return getattr(request.app.state, "database", None)


async def get_provider_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.provider_http_client  # type: ignore[no-any-return]


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
DatabaseDependency = Annotated[DatabaseManager | None, Depends(get_database)]
ProviderHttpClientDependency = Annotated[httpx.AsyncClient, Depends(get_provider_http_client)]


async def get_metrics(request: Request) -> MetricsRegistry:
    return request.app.state.metrics  # type: ignore[no-any-return]


MetricsDependency = Annotated[MetricsRegistry, Depends(get_metrics)]


def _safe_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _client_ip_hash(request: Request, pepper: str) -> str | None:
    if request.client is None:
        return None
    return hmac.new(pepper.encode(), request.client.host.encode(), hashlib.sha256).hexdigest()


async def get_tenant_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> TenantContext:
    """Authenticate a bearer key without placing the credential in logs or errors."""

    if database is None:
        raise DatabaseUnavailableError("The authentication database is unavailable")

    repository = PostgresSecurityRepository(database)
    request_id = request.headers.get("x-request-id", str(uuid4()))[:100]
    pepper = settings.auth_pepper.get_secret_value()
    raw_key = credentials.credentials if credentials is not None else None
    try:
        if credentials is None or credentials.scheme.lower() != "bearer" or not raw_key:
            raise MissingApiKeyError("A Bearer API key is required")
        return await AuthenticationService(repository, pepper).authenticate(
            raw_key, datetime.now(UTC)
        )
    except PrismError as error:
        await repository.record_security_event(
            event_type=error.code,
            key_fingerprint=_safe_fingerprint(raw_key) if raw_key else None,
            request_id=request_id,
            client_ip_hash=_client_ip_hash(request, pepper),
        )
        raise


async def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: SettingsDependency,
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise MissingApiKeyError("A Bearer admin API key is required")
    authenticate_admin(
        credentials.credentials,
        settings.admin_api_key.get_secret_value(),
    )


TenantDependency = Annotated[TenantContext, Depends(get_tenant_context)]
AdminDependency = Annotated[None, Depends(require_admin)]
